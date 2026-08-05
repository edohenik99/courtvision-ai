"""Strict I/O and create-once publication for verified NBA model builds."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
from typing import Any, NoReturn
from uuid import uuid4

from courtvision.prospective.contracts import (
    ConfigurationProvenanceV1,
    FrozenJSONMapping,
    GitProvenanceV1,
    ModelArtifactEntryV1,
    ModelBuildManifestV1,
    ProspectiveContractError,
    ProspectiveDigestMismatchError,
    ProspectiveUnverifiedModelError,
    TrainingProvenanceV1,
    canonical_json_bytes,
)
from courtvision.prospective.model_build import (
    FEATURE_SCHEMA_VERSION,
    MODEL_ID,
    ModelBuildSerializationError,
    VerifiedModelBuildError,
    validate_baseline_csv_bytes,
    validate_feature_schema_evidence,
    validate_training_input_evidence,
)


PLAYER_ARTIFACT_FILENAME = "player_baselines.csv"
TEAM_ARTIFACT_FILENAME = "team_baselines.csv"
TRAINING_INPUTS_FILENAME = "training_inputs_v1.json"
FEATURE_SCHEMA_FILENAME = "feature_schema_v1.json"
MODEL_BUILD_MANIFEST_FILENAME = "model_build_manifest_v1.json"
BUILD_STORE_LOCK_FILENAME = ".verified-build-store.lock"
STAGING_DIRECTORY_PREFIX = ".temporary-"

REQUIRED_BUILD_FILENAMES = frozenset(
    {
        PLAYER_ARTIFACT_FILENAME,
        TEAM_ARTIFACT_FILENAME,
        TRAINING_INPUTS_FILENAME,
        FEATURE_SCHEMA_FILENAME,
        MODEL_BUILD_MANIFEST_FILENAME,
    }
)


class ModelBuildValidationError(VerifiedModelBuildError):
    """Raised when an on-disk verified build fails closed validation."""


class ModelBuildPublicationError(VerifiedModelBuildError):
    """Raised when a candidate build cannot be atomically published."""


class ModelBuildConflictError(ModelBuildPublicationError):
    """Raised when a model version already names different or invalid bytes."""


class ModelBuildStoreBusyError(ModelBuildPublicationError):
    """Raised when another visible verified-build writer owns the store lock."""


class ModelBuildLockError(ModelBuildPublicationError):
    """Raised when build-store lock integrity or access cannot be established."""


class ModelBuildCleanupError(ModelBuildPublicationError):
    """Raised when the current attempt's staging directory cannot be removed."""


@dataclass(frozen=True, slots=True)
class VerifiedModelBuild:
    """A fully re-read and validated immutable build directory."""

    path: Path
    manifest: ModelBuildManifestV1
    replayed: bool = False


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _utc_clock_value(clock: Callable[[], datetime], field_name: str) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ProspectiveContractError(f"{field_name} clock must return timezone-aware UTC")
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ProspectiveContractError(f"{field_name} clock must return UTC")
    return value.astimezone(UTC)


def _parse_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ModelBuildValidationError(f"{field_name} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ModelBuildValidationError(f"{field_name} is not a valid timestamp") from exc
    if _format_utc(parsed) != value:
        raise ModelBuildValidationError(f"{field_name} is not canonical")
    return parsed


def _parse_date(value: object, field_name: str) -> date:
    if not isinstance(value, str):
        raise ModelBuildValidationError(f"{field_name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ModelBuildValidationError(f"{field_name} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ModelBuildValidationError(f"{field_name} must be a canonical ISO date")
    return parsed


def _reject_json_constant(value: str) -> NoReturn:
    raise ModelBuildValidationError(f"non-standard JSON numeric constant rejected: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModelBuildValidationError(f"duplicate JSON key rejected: {key}")
        result[key] = value
    return result


def _load_json_bytes(data: bytes, *, description: str) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ModelBuildValidationError(f"{description} must be valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except ModelBuildValidationError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ModelBuildValidationError(f"{description} is not valid strict JSON") from exc
    if not isinstance(value, dict):
        raise ModelBuildValidationError(f"{description} top level must be an object")
    try:
        canonical = canonical_json_bytes(value)
    except ProspectiveContractError as exc:
        raise ModelBuildValidationError(f"{description} is not canonical JSON") from exc
    if data != canonical:
        raise ModelBuildValidationError(f"{description} bytes are not canonical JSON")
    return value


def _read_bytes(path: Path, description: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ModelBuildValidationError(f"{description} is inaccessible") from exc


def _exact_fields(value: object, expected: set[str], description: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ModelBuildValidationError(f"{description} fields do not match the v1 contract")
    return value


_MANIFEST_FIELDS = {
    "schema_version",
    "model_id",
    "model_version",
    "sport",
    "league",
    "artifacts",
    "training",
    "build_git_provenance",
    "build_configuration_provenance",
    "feature_schema_version",
    "feature_schema_digest",
    "created_at_utc",
    "manifest_digest",
}


def _manifest_from_mapping(value: Mapping[str, Any]) -> ModelBuildManifestV1:
    raw = _exact_fields(dict(value), _MANIFEST_FIELDS, "manifest")
    artifacts_value = raw["artifacts"]
    if not isinstance(artifacts_value, list):
        raise ModelBuildValidationError("manifest artifacts must be a list")
    artifacts: list[ModelArtifactEntryV1] = []
    for index, item in enumerate(artifacts_value):
        artifact = _exact_fields(
            item,
            {"logical_name", "repository_relative_path", "sha256", "size_bytes"},
            f"manifest artifact {index}",
        )
        artifacts.append(ModelArtifactEntryV1(**artifact))
    training_value = _exact_fields(
        raw["training"],
        {
            "training_start_date",
            "training_end_date",
            "training_completed_at_utc",
            "training_run_id",
            "training_data_digest",
            "model_build_tool_version",
        },
        "manifest training",
    )
    training = TrainingProvenanceV1(
        training_start_date=_parse_date(
            training_value["training_start_date"], "training_start_date"
        ),
        training_end_date=_parse_date(
            training_value["training_end_date"], "training_end_date"
        ),
        training_completed_at_utc=_parse_utc(
            training_value["training_completed_at_utc"],
            "training_completed_at_utc",
        ),
        training_run_id=training_value["training_run_id"],
        training_data_digest=training_value["training_data_digest"],
        model_build_tool_version=training_value["model_build_tool_version"],
    )
    git_value = _exact_fields(
        raw["build_git_provenance"],
        {"commit_sha", "dirty", "working_tree_fingerprint"},
        "build_git_provenance",
    )
    git = GitProvenanceV1(**git_value)
    configuration_value = _exact_fields(
        raw["build_configuration_provenance"],
        {"canonical_configuration", "configuration_digest"},
        "build_configuration_provenance",
    )
    canonical_configuration = configuration_value["canonical_configuration"]
    if not isinstance(canonical_configuration, dict):
        raise ModelBuildValidationError("canonical_configuration must be an object")
    configuration = ConfigurationProvenanceV1(
        canonical_configuration=FrozenJSONMapping(canonical_configuration),
        configuration_digest=configuration_value["configuration_digest"],
    )
    return ModelBuildManifestV1(
        schema_version=raw["schema_version"],
        model_id=raw["model_id"],
        model_version=raw["model_version"],
        sport=raw["sport"],
        league=raw["league"],
        artifacts=tuple(artifacts),
        training=training,
        build_git_provenance=git,
        build_configuration_provenance=configuration,
        feature_schema_version=raw["feature_schema_version"],
        feature_schema_digest=raw["feature_schema_digest"],
        created_at_utc=_parse_utc(raw["created_at_utc"], "created_at_utc"),
        manifest_digest=raw["manifest_digest"],
    )


def load_model_build_manifest(path: str | Path) -> ModelBuildManifestV1:
    """Strictly load a canonical manifest and reconstruct every nested contract."""

    manifest_path = Path(path)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ModelBuildValidationError("model-build manifest must be a regular file")
    raw = _load_json_bytes(
        _read_bytes(manifest_path, "model-build manifest"),
        description="model-build manifest",
    )
    try:
        return _manifest_from_mapping(raw)
    except ModelBuildValidationError:
        raise
    except (ProspectiveContractError, TypeError, ValueError) as exc:
        raise ModelBuildValidationError("model-build manifest contract is invalid") from exc


def _repository_root(value: str | Path) -> Path:
    try:
        root = Path(value).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ModelBuildPublicationError("repository_root is inaccessible") from exc
    if not root.is_dir():
        raise ModelBuildPublicationError("repository_root must be a directory")
    return root


def _path_inside_repository(repository_root: Path, value: str | Path, field_name: str) -> Path:
    supplied = Path(value)
    candidate = supplied if supplied.is_absolute() else repository_root / supplied
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(repository_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ModelBuildPublicationError(f"{field_name} must be inside repository_root") from exc
    return resolved


def _validate_directory_entries(build_directory: Path) -> None:
    if build_directory.is_symlink():
        raise ModelBuildValidationError("verified build directory may not be a symlink")
    if not build_directory.is_dir():
        raise ModelBuildValidationError("verified build destination is not a directory")
    try:
        entries = list(build_directory.iterdir())
    except OSError as exc:
        raise ModelBuildValidationError("verified build directory is inaccessible") from exc
    names: set[str] = set()
    for entry in entries:
        if entry.is_symlink():
            raise ModelBuildValidationError(f"symlink is forbidden in verified build: {entry.name}")
        if not entry.is_file():
            raise ModelBuildValidationError(f"non-file entry is forbidden in verified build: {entry.name}")
        names.add(entry.name)
    if names != REQUIRED_BUILD_FILENAMES:
        missing = sorted(REQUIRED_BUILD_FILENAMES - names)
        unexpected = sorted(names - REQUIRED_BUILD_FILENAMES)
        raise ModelBuildValidationError(
            f"verified build file set is invalid; missing={missing}, unexpected={unexpected}"
        )


def _artifact_relative_path(repository_root: Path, final_directory: Path, filename: str) -> str:
    try:
        return (final_directory / filename).relative_to(repository_root).as_posix()
    except ValueError as exc:
        raise ModelBuildValidationError("verified build artifact path is outside repository") from exc


def _validate_build_directory(
    build_directory: Path,
    *,
    repository_root: Path,
    expected_final_directory: Path,
    replayed: bool = False,
) -> VerifiedModelBuild:
    _validate_directory_entries(build_directory)
    manifest = load_model_build_manifest(
        build_directory / MODEL_BUILD_MANIFEST_FILENAME
    )
    if manifest.model_id != MODEL_ID or manifest.sport != "NBA" or manifest.league != "NBA":
        raise ModelBuildValidationError("manifest is not the CourtVision NBA baseline v1 model")
    if manifest.model_version != expected_final_directory.name:
        raise ModelBuildValidationError("manifest model_version does not match destination")
    training_raw = _load_json_bytes(
        _read_bytes(build_directory / TRAINING_INPUTS_FILENAME, "training-input evidence"),
        description="training-input evidence",
    )
    feature_raw = _load_json_bytes(
        _read_bytes(build_directory / FEATURE_SCHEMA_FILENAME, "feature-schema evidence"),
        description="feature-schema evidence",
    )
    try:
        training = validate_training_input_evidence(training_raw)
        feature = validate_feature_schema_evidence(feature_raw)
    except ProspectiveContractError as exc:
        raise ModelBuildValidationError("supporting build evidence is invalid") from exc
    if manifest.training.training_start_date.isoformat() != training["requested_start_date"]:
        raise ModelBuildValidationError("manifest training start does not match training evidence")
    if manifest.training.training_end_date.isoformat() != training["requested_end_date"]:
        raise ModelBuildValidationError("manifest training end does not match training evidence")
    if manifest.training.training_data_digest != training["training_data_digest"]:
        raise ProspectiveDigestMismatchError(
            "manifest training_data_digest does not match training evidence"
        )
    if manifest.feature_schema_version != feature["feature_schema_version"]:
        raise ModelBuildValidationError("manifest feature schema version does not match evidence")
    if manifest.feature_schema_digest != feature["feature_schema_digest"]:
        raise ProspectiveDigestMismatchError(
            "manifest feature_schema_digest does not match feature evidence"
        )
    if manifest.feature_schema_version != FEATURE_SCHEMA_VERSION:
        raise ModelBuildValidationError("manifest feature schema version is unsupported")
    represented = {artifact.logical_name: artifact for artifact in manifest.artifacts}
    if set(represented) != {"player_baseline", "team_baseline"}:
        raise ModelBuildValidationError("manifest must represent exactly player and team artifacts")
    artifact_specs = {
        "player_baseline": (PLAYER_ARTIFACT_FILENAME, "player"),
        "team_baseline": (TEAM_ARTIFACT_FILENAME, "team"),
    }
    for logical_name, (filename, kind) in artifact_specs.items():
        artifact = represented[logical_name]
        expected_path = _artifact_relative_path(
            repository_root, expected_final_directory, filename
        )
        if artifact.repository_relative_path != expected_path:
            raise ModelBuildValidationError(
                f"manifest artifact path is not the final repository-relative path: {logical_name}"
            )
        data = _read_bytes(build_directory / filename, f"{kind} baseline artifact")
        try:
            validate_baseline_csv_bytes(data, kind=kind)
        except ModelBuildSerializationError as exc:
            raise ModelBuildValidationError(f"{kind} baseline artifact is invalid") from exc
        digest = hashlib.sha256(data).hexdigest()
        if digest != artifact.sha256 or len(data) != artifact.size_bytes:
            raise ProspectiveDigestMismatchError(
                f"artifact digest or size does not match manifest: {logical_name}"
            )
    return VerifiedModelBuild(
        path=expected_final_directory.resolve(strict=False),
        manifest=manifest,
        replayed=replayed,
    )


def validate_verified_model_build(
    build_directory: str | Path,
    *,
    repository_root: str | Path,
) -> VerifiedModelBuild:
    """Re-read and validate exactly five immutable files in a final build."""

    supplied = Path(build_directory)
    if supplied.is_symlink():
        raise ModelBuildValidationError("verified build directory may not be a symlink")
    root = _repository_root(repository_root)
    directory = _path_inside_repository(root, supplied, "build_directory")
    return _validate_build_directory(
        directory,
        repository_root=root,
        expected_final_directory=directory,
    )


def load_verified_model_build(
    build_directory: str | Path,
    *,
    repository_root: str | Path,
) -> VerifiedModelBuild:
    """Load only after complete manifest, evidence, and artifact validation."""

    return validate_verified_model_build(
        build_directory, repository_root=repository_root
    )


def _write_exclusive(path: Path, data: bytes, description: str) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ModelBuildPublicationError(f"failed to write staged {description}") from exc


def _artifact_entry(
    path: Path,
    *,
    logical_name: str,
    repository_relative_path: str,
) -> ModelArtifactEntryV1:
    data = _read_bytes(path, logical_name)
    return ModelArtifactEntryV1(
        logical_name=logical_name,
        repository_relative_path=repository_relative_path,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


_LOCK_FIELDS = {
    "schema_version",
    "owner_token",
    "process_id",
    "host",
    "created_at_utc",
    "training_run_id",
}


def _load_lock(path: Path) -> tuple[dict[str, Any], bytes, os.stat_result]:
    try:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise ModelBuildLockError("build-store lock is inaccessible") from exc
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ModelBuildLockError("build-store lock changed while being read")
    raw = _load_json_bytes(data, description="build-store lock")
    metadata = _exact_fields(raw, _LOCK_FIELDS, "build-store lock")
    if metadata["schema_version"] != 1:
        raise ModelBuildLockError("build-store lock schema is unsupported")
    if type(metadata["process_id"]) is not int or metadata["process_id"] <= 0:
        raise ModelBuildLockError("build-store lock process_id is invalid")
    for field_name in ("owner_token", "host", "training_run_id"):
        if not isinstance(metadata[field_name], str) or not metadata[field_name]:
            raise ModelBuildLockError(f"build-store lock {field_name} is invalid")
    _parse_utc(metadata["created_at_utc"], "build-store lock created_at_utc")
    return metadata, data, after


def _classify_existing_lock(path: Path) -> NoReturn:
    try:
        metadata, _, _ = _load_lock(path)
    except (ModelBuildValidationError, ModelBuildLockError) as exc:
        raise ModelBuildLockError("existing build-store lock is malformed or inaccessible") from exc
    raise ModelBuildStoreBusyError(
        "verified build store is busy "
        f"(pid={metadata['process_id']}, host={metadata['host']})"
    )


class _BuildStoreLock:
    def __init__(
        self,
        path: Path,
        *,
        training_run_id: str,
        clock: Callable[[], datetime],
    ) -> None:
        self.path = path
        self.metadata = {
            "schema_version": 1,
            "owner_token": uuid4().hex,
            "process_id": os.getpid(),
            "host": socket.gethostname() or "unknown-host",
            "created_at_utc": _format_utc(
                _utc_clock_value(clock, "build-store lock creation")
            ),
            "training_run_id": training_run_id,
        }
        self.data = canonical_json_bytes(self.metadata)
        self.acquired = False

    def acquire(self) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except FileExistsError:
            _classify_existing_lock(self.path)
        except PermissionError as exc:
            try:
                visible = os.path.lexists(self.path)
            except OSError as visibility_exc:
                raise ModelBuildLockError(
                    "build-store lock visibility is inaccessible"
                ) from visibility_exc
            if visible:
                _classify_existing_lock(self.path)
            raise ModelBuildLockError("cannot create build-store lock") from exc
        except OSError as exc:
            raise ModelBuildLockError("cannot create build-store lock") from exc
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(self.data)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise ModelBuildLockError(
                "failed to initialize build-store lock; malformed lock was left in place"
            ) from exc
        try:
            metadata, data, _ = _load_lock(self.path)
        except (ModelBuildValidationError, ModelBuildLockError) as exc:
            raise ModelBuildLockError(
                "new build-store lock could not be verified; lock was left in place"
            ) from exc
        if metadata != self.metadata or data != self.data:
            raise ModelBuildLockError(
                "new build-store lock ownership could not be verified; lock was left in place"
            )
        self.acquired = True

    def release(self) -> None:
        if not self.acquired:
            return
        metadata, data, observed = _load_lock(self.path)
        if metadata != self.metadata or data != self.data:
            raise ModelBuildLockError(
                "build-store lock ownership changed; refusing to remove another owner's lock"
            )
        try:
            current = self.path.stat()
        except OSError as exc:
            raise ModelBuildLockError("owned build-store lock became inaccessible") from exc
        if (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        ) != (
            observed.st_dev,
            observed.st_ino,
            observed.st_size,
            observed.st_mtime_ns,
        ):
            raise ModelBuildLockError(
                "build-store lock changed before release; refusing removal"
            )
        try:
            self.path.unlink()
        except OSError as exc:
            raise ModelBuildLockError("verified lock owner could not remove its lock") from exc
        self.acquired = False

    def __enter__(self) -> _BuildStoreLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def _remove_owned_staging(stage: Path, store_root: Path) -> None:
    if stage.parent != store_root or not stage.name.startswith(STAGING_DIRECTORY_PREFIX):
        raise ModelBuildCleanupError("refusing to clean an unrecognized staging path")
    if stage.is_symlink():
        raise ModelBuildCleanupError("staging path became a symlink; refusing cleanup")
    try:
        shutil.rmtree(stage)
    except OSError as exc:
        raise ModelBuildCleanupError(
            f"could not remove current staging directory: {stage.name}"
        ) from exc


def _same_build_bytes(first: Path, second: Path) -> bool:
    for filename in sorted(REQUIRED_BUILD_FILENAMES):
        try:
            if (first / filename).read_bytes() != (second / filename).read_bytes():
                return False
        except OSError as exc:
            raise ModelBuildConflictError("could not compare replay build bytes") from exc
    return True


def publish_verified_model_build(
    *,
    repository_root: str | Path,
    output_root: str | Path,
    model_version: str,
    training_run_id: str,
    training_start_date: date,
    training_end_date: date,
    training_input_evidence: Mapping[str, Any],
    feature_schema_evidence: Mapping[str, Any],
    player_baseline_bytes: bytes,
    team_baseline_bytes: bytes,
    build_git_provenance: GitProvenanceV1,
    build_configuration_provenance: ConfigurationProvenanceV1,
    model_build_tool_version: str,
    clock: Callable[[], datetime],
) -> VerifiedModelBuild:
    """Stage, validate, lock, atomically rename, and finally revalidate a build."""

    root = _repository_root(repository_root)
    output = _path_inside_repository(root, output_root, "output_root")
    store_root = output / "model" / "verified_builds"
    try:
        store_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ModelBuildPublicationError("cannot create verified build store") from exc
    if store_root.is_symlink() or not store_root.is_dir():
        raise ModelBuildPublicationError("verified build store must be a real directory")
    stage = store_root / f"{STAGING_DIRECTORY_PREFIX}{training_run_id}"
    final = store_root / model_version
    if os.path.lexists(stage):
        raise ModelBuildPublicationError(
            "training_run_id staging directory already exists; it was not modified"
        )
    try:
        stage.mkdir()
    except OSError as exc:
        raise ModelBuildPublicationError("cannot create unique build staging directory") from exc
    stage_created = True
    try:
        training = validate_training_input_evidence(training_input_evidence)
        feature = validate_feature_schema_evidence(feature_schema_evidence)
        _write_exclusive(
            stage / PLAYER_ARTIFACT_FILENAME,
            player_baseline_bytes,
            PLAYER_ARTIFACT_FILENAME,
        )
        _write_exclusive(
            stage / TEAM_ARTIFACT_FILENAME,
            team_baseline_bytes,
            TEAM_ARTIFACT_FILENAME,
        )
        _write_exclusive(
            stage / TRAINING_INPUTS_FILENAME,
            canonical_json_bytes(training),
            TRAINING_INPUTS_FILENAME,
        )
        _write_exclusive(
            stage / FEATURE_SCHEMA_FILENAME,
            canonical_json_bytes(feature),
            FEATURE_SCHEMA_FILENAME,
        )
        completed_at = _utc_clock_value(clock, "training completion")
        training_provenance = TrainingProvenanceV1(
            training_start_date=training_start_date,
            training_end_date=training_end_date,
            training_completed_at_utc=completed_at,
            training_run_id=training_run_id,
            training_data_digest=training["training_data_digest"],
            model_build_tool_version=model_build_tool_version,
        )
        artifacts = (
            _artifact_entry(
                stage / PLAYER_ARTIFACT_FILENAME,
                logical_name="player_baseline",
                repository_relative_path=_artifact_relative_path(
                    root, final, PLAYER_ARTIFACT_FILENAME
                ),
            ),
            _artifact_entry(
                stage / TEAM_ARTIFACT_FILENAME,
                logical_name="team_baseline",
                repository_relative_path=_artifact_relative_path(
                    root, final, TEAM_ARTIFACT_FILENAME
                ),
            ),
        )
        created_at = _utc_clock_value(clock, "manifest creation")
        manifest = ModelBuildManifestV1.create(
            model_id=MODEL_ID,
            model_version=model_version,
            sport="NBA",
            league="NBA",
            artifacts=artifacts,
            training=training_provenance,
            build_git_provenance=build_git_provenance,
            build_configuration_provenance=build_configuration_provenance,
            feature_schema_version=feature["feature_schema_version"],
            feature_schema_digest=feature["feature_schema_digest"],
            created_at_utc=created_at,
        )
        _write_exclusive(
            stage / MODEL_BUILD_MANIFEST_FILENAME,
            canonical_json_bytes(manifest.to_dict()),
            MODEL_BUILD_MANIFEST_FILENAME,
        )
        candidate = _validate_build_directory(
            stage,
            repository_root=root,
            expected_final_directory=final,
        )
        lock = _BuildStoreLock(
            store_root / BUILD_STORE_LOCK_FILENAME,
            training_run_id=training_run_id,
            clock=clock,
        )
        with lock:
            if os.path.lexists(final):
                try:
                    existing = _validate_build_directory(
                        final,
                        repository_root=root,
                        expected_final_directory=final,
                        replayed=True,
                    )
                except Exception as exc:
                    raise ModelBuildConflictError(
                        "existing model-version destination is invalid; refusing repair or overwrite"
                    ) from exc
                if existing.manifest.to_dict() != candidate.manifest.to_dict():
                    raise ModelBuildConflictError(
                        "existing model version has different manifest content"
                    )
                if not _same_build_bytes(stage, final):
                    raise ModelBuildConflictError(
                        "existing model version has different artifact or evidence bytes"
                    )
                _remove_owned_staging(stage, store_root)
                stage_created = False
                return existing
            try:
                os.rename(stage, final)
            except PermissionError as exc:
                raise ModelBuildPublicationError(
                    "atomic build-directory rename was denied; existing builds were untouched"
                ) from exc
            except OSError as exc:
                raise ModelBuildPublicationError(
                    "atomic build-directory rename failed; existing builds were untouched"
                ) from exc
            stage_created = False
            return _validate_build_directory(
                final,
                repository_root=root,
                expected_final_directory=final,
            )
    except Exception as original:
        if stage_created and os.path.lexists(stage):
            try:
                _remove_owned_staging(stage, store_root)
            except ModelBuildCleanupError as cleanup:
                raise cleanup from original
        raise


__all__ = [
    "BUILD_STORE_LOCK_FILENAME",
    "FEATURE_SCHEMA_FILENAME",
    "MODEL_BUILD_MANIFEST_FILENAME",
    "PLAYER_ARTIFACT_FILENAME",
    "REQUIRED_BUILD_FILENAMES",
    "TEAM_ARTIFACT_FILENAME",
    "TRAINING_INPUTS_FILENAME",
    "ModelBuildCleanupError",
    "ModelBuildConflictError",
    "ModelBuildLockError",
    "ModelBuildPublicationError",
    "ModelBuildStoreBusyError",
    "ModelBuildValidationError",
    "VerifiedModelBuild",
    "load_model_build_manifest",
    "load_verified_model_build",
    "validate_verified_model_build",
]
