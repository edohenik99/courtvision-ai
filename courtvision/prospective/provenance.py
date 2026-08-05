"""Read-only provenance capture for prospective NBA paper-trial cohorts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import os
from pathlib import Path
import subprocess
from typing import Any

from courtvision.prospective.contracts import (
    ConfigurationProvenanceV1,
    GitProvenanceV1,
    ModelArtifactEntryV1,
    ModelBuildManifestV1,
    ProspectiveCohortIdentityV1,
    ProspectiveCohortSpecV1,
    ProspectiveContractError,
    ProspectiveDigestMismatchError,
    ProspectiveDirtyTreeError,
    ProspectiveMissingArtifactError,
    ProspectiveProvenanceError,
    ProspectiveUnverifiedModelError,
    canonical_sha256,
    derive_prospective_cohort_identity,
)


APPROVED_GENERATED_GIT_EXCLUSIONS = (
    "data/lifecycle",
    "outputs",
    "test_outputs",
)
WORKING_TREE_FINGERPRINT_VERSION = "prospective_working_tree_v1"


def _run_git(arguments: Iterable[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "--no-optional-locks", *arguments],
            cwd=cwd,
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProspectiveProvenanceError(
            "Git provenance is unavailable; cohort activation is blocked"
        ) from exc


def _require_git_success(
    result: subprocess.CompletedProcess[bytes], operation: str
) -> bytes:
    if result.returncode != 0:
        raise ProspectiveProvenanceError(
            f"Git {operation} failed or is unknown; cohort activation is blocked"
        )
    return result.stdout


def resolve_repository_root(start: str | Path) -> Path:
    """Resolve a Git root without changing the process working directory."""

    candidate = Path(start)
    if not candidate.exists():
        raise ProspectiveProvenanceError(
            "repository path does not exist; cohort activation is blocked"
        )
    cwd = candidate if candidate.is_dir() else candidate.parent
    output = _require_git_success(
        _run_git(("rev-parse", "--show-toplevel"), cwd),
        "repository-root resolution",
    )
    try:
        root = Path(output.decode("utf-8", errors="strict").strip()).resolve(
            strict=True
        )
    except (UnicodeError, OSError, RuntimeError) as exc:
        raise ProspectiveProvenanceError(
            "Git repository root is invalid or inaccessible; cohort activation is blocked"
        ) from exc
    if not root.is_dir():
        raise ProspectiveProvenanceError(
            "Git repository root is not a directory; cohort activation is blocked"
        )
    return root


def _git_scope_pathspec() -> tuple[str, ...]:
    return (
        "--",
        ".",
        *(f":(exclude){path}" for path in APPROVED_GENERATED_GIT_EXCLUSIONS),
    )


def _status_bytes(root: Path) -> bytes:
    return _require_git_success(
        _run_git(
            (
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignore-submodules=none",
                *_git_scope_pathspec(),
            ),
            root,
        ),
        "working-tree status",
    )


def _repository_paths(root: Path) -> tuple[str, ...]:
    output = _require_git_success(
        _run_git(
            (
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                *_git_scope_pathspec(),
            ),
            root,
        ),
        "working-tree enumeration",
    )
    try:
        values = {
            item.decode("utf-8", errors="strict").replace("\\", "/")
            for item in output.split(b"\0")
            if item
        }
    except UnicodeError as exc:
        raise ProspectiveProvenanceError(
            "Git returned a non-UTF-8 repository path; cohort activation is blocked"
        ) from exc
    if any(path.startswith("/") or "../" in f"{path}/" for path in values):
        raise ProspectiveProvenanceError(
            "Git returned an unsafe repository path; cohort activation is blocked"
        )
    return tuple(sorted(values))


def _git_index_entries(root: Path) -> list[dict[str, object]]:
    """Return clean tracked content as Git stores it, independent of filters."""

    output = _require_git_success(
        _run_git(
            (
                "ls-files",
                "--stage",
                "-z",
                "--cached",
                *_git_scope_pathspec(),
            ),
            root,
        ),
        "index enumeration",
    )
    entries: list[dict[str, object]] = []
    try:
        for item in output.split(b"\0"):
            if not item:
                continue
            metadata, encoded_path = item.split(b"\t", 1)
            mode, object_id, stage = metadata.decode("ascii").split(" ", 2)
            relative_path = encoded_path.decode("utf-8", errors="strict").replace(
                "\\", "/"
            )
            entries.append(
                {
                    "path": relative_path,
                    "mode": mode,
                    "object_id": object_id,
                    "stage": stage,
                }
            )
    except (UnicodeError, ValueError) as exc:
        raise ProspectiveProvenanceError(
            "Git index evidence is malformed; cohort activation is blocked"
        ) from exc
    return sorted(entries, key=lambda entry: str(entry["path"]))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ProspectiveProvenanceError(
            "a provenance file became inaccessible; cohort activation is blocked"
        ) from exc
    return digest.hexdigest()


def _working_tree_entries(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for relative_path in _repository_paths(root):
        path = root / Path(relative_path)
        if path.is_symlink():
            try:
                target = os.readlink(path)
            except OSError as exc:
                raise ProspectiveProvenanceError(
                    "a repository symlink became inaccessible; cohort activation is blocked"
                ) from exc
            encoded_target = os.fsencode(target)
            entries.append(
                {
                    "path": relative_path,
                    "kind": "symlink",
                    "sha256": hashlib.sha256(encoded_target).hexdigest(),
                    "size_bytes": len(encoded_target),
                }
            )
        elif path.is_file():
            try:
                before = path.stat()
                digest = _file_sha256(path)
                after = path.stat()
            except OSError as exc:
                raise ProspectiveProvenanceError(
                    "a repository file became inaccessible; cohort activation is blocked"
                ) from exc
            if (
                before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise ProspectiveProvenanceError(
                    "the working tree changed during provenance capture; activation is blocked"
                )
            entries.append(
                {
                    "path": relative_path,
                    "kind": "file",
                    "sha256": digest,
                    "size_bytes": after.st_size,
                }
            )
        elif path.is_dir():
            entries.append({"path": relative_path, "kind": "gitlink"})
        else:
            entries.append({"path": relative_path, "kind": "missing"})
    return entries


def capture_git_provenance(
    repository: str | Path,
    *,
    require_clean: bool = True,
) -> GitProvenanceV1:
    """Capture current commit and a path-independent working-tree fingerprint.

    Git optional locks are disabled. Only the three approved generated roots
    above are excluded; source, configuration, and data/history changes remain
    visible. Unknown state always fails closed. Dirty state fails by default.
    """

    root = resolve_repository_root(repository)
    commit_output = _require_git_success(
        _run_git(("rev-parse", "--verify", "HEAD^{commit}"), root),
        "HEAD resolution",
    )
    try:
        commit_sha = commit_output.decode("ascii", errors="strict").strip()
    except UnicodeError as exc:
        raise ProspectiveProvenanceError(
            "Git commit identity is invalid; cohort activation is blocked"
        ) from exc
    first_status = _status_bytes(root)
    dirty = bool(first_status)
    entries = _working_tree_entries(root) if dirty else _git_index_entries(root)
    second_status = _status_bytes(root)
    second_commit = _require_git_success(
        _run_git(("rev-parse", "--verify", "HEAD^{commit}"), root),
        "HEAD revalidation",
    ).strip()
    if first_status != second_status or second_commit != commit_output.strip():
        raise ProspectiveProvenanceError(
            "Git state changed during provenance capture; cohort activation is blocked"
        )
    fingerprint = canonical_sha256(
        {
            "fingerprint_version": WORKING_TREE_FINGERPRINT_VERSION,
            "representation": "working_files" if dirty else "git_index",
            "excluded_generated_directories": list(
                APPROVED_GENERATED_GIT_EXCLUSIONS
            ),
            "entries": entries,
        }
    )
    provenance = GitProvenanceV1(
        commit_sha=commit_sha,
        dirty=dirty,
        working_tree_fingerprint=fingerprint,
    )
    if dirty and require_clean:
        raise ProspectiveDirtyTreeError(
            "dirty Git state blocks prospective cohort activation; commit, remove, "
            "or explicitly preserve the changes before retrying"
        )
    return provenance


def _artifact_path(repository_root: Path, supplied_path: str | Path) -> tuple[Path, str]:
    raw = Path(supplied_path)
    candidate = raw if raw.is_absolute() else repository_root / raw
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(repository_root).as_posix()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProspectiveMissingArtifactError(
            "model artifact is missing, inaccessible, or outside the repository"
        ) from exc
    if not resolved.is_file():
        raise ProspectiveMissingArtifactError(
            f"model artifact is not a file: {relative}"
        )
    return resolved, relative


def capture_model_artifacts(
    repository_root: str | Path,
    artifact_paths: Mapping[str, str | Path],
) -> tuple[ModelArtifactEntryV1, ...]:
    """Hash explicitly supplied model files without modifying their contents."""

    root = Path(repository_root).resolve(strict=True)
    if not root.is_dir():
        raise ProspectiveProvenanceError("repository_root must be a directory")
    if not isinstance(artifact_paths, Mapping) or not artifact_paths:
        raise ProspectiveUnverifiedModelError(
            "an explicit non-empty logical-name to artifact-path mapping is required"
        )
    artifacts: list[ModelArtifactEntryV1] = []
    for logical_name in sorted(artifact_paths):
        if not isinstance(logical_name, str):
            raise ProspectiveContractError("artifact logical names must be strings")
        path, relative = _artifact_path(root, artifact_paths[logical_name])
        try:
            before = path.stat()
            digest = _file_sha256(path)
            after = path.stat()
        except OSError as exc:
            raise ProspectiveMissingArtifactError(
                f"model artifact became inaccessible: {relative}"
            ) from exc
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise ProspectiveProvenanceError(
                f"model artifact changed during hashing: {relative}"
            )
        artifacts.append(
            ModelArtifactEntryV1(
                logical_name=logical_name,
                repository_relative_path=relative,
                sha256=digest,
                size_bytes=after.st_size,
            )
        )
    return tuple(artifacts)


def hash_model_artifacts(
    repository_root: str | Path,
    artifact_paths: Mapping[str, str | Path],
) -> tuple[ModelArtifactEntryV1, ...]:
    """Compatibility name for explicit read-only artifact capture."""

    return capture_model_artifacts(repository_root, artifact_paths)


def capture_configuration_provenance(
    configuration: Mapping[str, Any],
) -> ConfigurationProvenanceV1:
    """Canonicalize supplied prediction configuration without reading .env."""

    return ConfigurationProvenanceV1.from_configuration(configuration)


def validate_model_build_manifest(
    manifest: ModelBuildManifestV1,
    repository_root: str | Path,
    *,
    required_artifact_names: Iterable[str] | None = None,
) -> ModelBuildManifestV1:
    """Verify manifest integrity and all represented artifact bytes in place."""

    if not isinstance(manifest, ModelBuildManifestV1):
        raise ProspectiveUnverifiedModelError(
            "an explicit valid ModelBuildManifestV1 is required; current legacy "
            "model files are not automatically verified"
        )
    represented = {artifact.logical_name: artifact for artifact in manifest.artifacts}
    required = (
        set(represented)
        if required_artifact_names is None
        else {str(name).strip() for name in required_artifact_names if str(name).strip()}
    )
    missing_names = sorted(required.difference(represented))
    if missing_names:
        raise ProspectiveUnverifiedModelError(
            "model-build manifest omits required artifact logical names: "
            + ", ".join(missing_names)
        )
    captured = capture_model_artifacts(
        repository_root,
        {
            name: represented[name].repository_relative_path
            for name in sorted(represented)
        },
    )
    actual = {artifact.logical_name: artifact for artifact in captured}
    for name, claimed in represented.items():
        observed = actual[name]
        if observed.repository_relative_path != claimed.repository_relative_path:
            raise ProspectiveDigestMismatchError(
                f"artifact repository path does not match manifest: {name}"
            )
        if observed.sha256 != claimed.sha256 or observed.size_bytes != claimed.size_bytes:
            raise ProspectiveDigestMismatchError(
                f"artifact digest or size does not match manifest: {name}"
            )
    if canonical_sha256(manifest.content_without_digest()) != manifest.manifest_digest:
        raise ProspectiveDigestMismatchError(
            "manifest_digest does not match canonical model-build content"
        )
    return manifest


def validate_git_provenance(
    expected: GitProvenanceV1,
    repository: str | Path,
) -> GitProvenanceV1:
    """Require expected Git evidence to equal the current clean repository."""

    if not isinstance(expected, GitProvenanceV1):
        raise ProspectiveProvenanceError("GitProvenanceV1 is required")
    if expected.dirty:
        raise ProspectiveDirtyTreeError(
            "dirty Git provenance blocks prospective cohort activation"
        )
    current = capture_git_provenance(repository, require_clean=True)
    if current.commit_sha != expected.commit_sha:
        raise ProspectiveDigestMismatchError(
            "cohort Git commit does not match the current repository commit"
        )
    if current.working_tree_fingerprint != expected.working_tree_fingerprint:
        raise ProspectiveDigestMismatchError(
            "cohort working-tree fingerprint does not match the current repository"
        )
    return current


def validate_and_derive_cohort_identity(
    spec: ProspectiveCohortSpecV1,
    repository_root: str | Path,
    *,
    required_artifact_names: Iterable[str] | None = None,
) -> ProspectiveCohortIdentityV1:
    """Fail closed, verify current read-only evidence, then derive identity."""

    if not isinstance(spec, ProspectiveCohortSpecV1):
        raise ProspectiveContractError("ProspectiveCohortSpecV1 is required")
    validate_git_provenance(spec.git_provenance, repository_root)
    validate_model_build_manifest(
        spec.model_build_manifest,
        repository_root,
        required_artifact_names=required_artifact_names,
    )
    return derive_prospective_cohort_identity(spec)


__all__ = [
    "APPROVED_GENERATED_GIT_EXCLUSIONS",
    "WORKING_TREE_FINGERPRINT_VERSION",
    "capture_configuration_provenance",
    "capture_git_provenance",
    "capture_model_artifacts",
    "hash_model_artifacts",
    "resolve_repository_root",
    "validate_and_derive_cohort_identity",
    "validate_git_provenance",
    "validate_model_build_manifest",
]
