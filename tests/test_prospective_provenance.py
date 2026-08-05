from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
from pathlib import Path
import subprocess

import pytest

from courtvision.prospective.contracts import (
    ModelArtifactEntryV1,
    ModelBuildManifestV1,
    ProspectiveDigestMismatchError,
    ProspectiveDirtyTreeError,
    ProspectiveMissingArtifactError,
    ProspectiveProvenanceError,
    ProspectiveSecretConfigurationError,
    ProspectiveUnverifiedModelError,
    TrainingProvenanceV1,
)
from courtvision.prospective.provenance import (
    capture_configuration_provenance,
    capture_git_provenance,
    capture_model_artifacts,
    resolve_repository_root,
    validate_model_build_manifest,
)


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _initialize_repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "--quiet")
    _git(path, "config", "user.email", "prospective-tests@example.invalid")
    _git(path, "config", "user.name", "Prospective Tests")
    (path / "src").mkdir()
    (path / "models").mkdir()
    (path / "src" / "model_code.py").write_text(
        "MODEL_VERSION = 'v1'\n", encoding="utf-8", newline="\n"
    )
    (path / "models" / "model.bin").write_bytes(b"synthetic-model-v1")
    _git(path, "add", "src/model_code.py", "models/model.bin")
    environment = {
        "GIT_AUTHOR_DATE": "2026-08-05T12:00:00Z",
        "GIT_COMMITTER_DATE": "2026-08-05T12:00:00Z",
    }
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "synthetic baseline"],
        cwd=path,
        env={**__import__("os").environ, **environment},
        capture_output=True,
        check=True,
    )
    return path


@pytest.fixture
def clean_repository(tmp_path: Path) -> Path:
    return _initialize_repository(tmp_path / "repository")


def _training() -> TrainingProvenanceV1:
    return TrainingProvenanceV1(
        training_start_date=date(2024, 10, 1),
        training_end_date=date(2025, 6, 30),
        training_completed_at_utc=datetime(2026, 8, 5, 12, tzinfo=UTC),
        training_run_id="synthetic-training-run",
        training_data_digest="a" * 64,
        model_build_tool_version="synthetic-trainer-1",
    )


def _manifest(
    artifacts: tuple[ModelArtifactEntryV1, ...],
) -> ModelBuildManifestV1:
    return ModelBuildManifestV1.create(
        model_id="synthetic-nba-model",
        model_version="v1",
        sport="NBA",
        league="NBA",
        artifacts=artifacts,
        training=_training(),
        feature_schema_version="synthetic-features-v1",
        feature_schema_digest="b" * 64,
        created_at_utc=datetime(2026, 8, 5, 12, tzinfo=UTC),
    )


def _snapshot_tree(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            snapshot[relative + "/"] = "directory"
    return snapshot


def test_clean_git_capture_resolves_current_commit_and_does_not_change_cwd(
    clean_repository: Path,
) -> None:
    original_cwd = Path.cwd()
    provenance = capture_git_provenance(clean_repository)
    assert Path.cwd() == original_cwd
    assert provenance.commit_sha == _git(clean_repository, "rev-parse", "HEAD")
    assert provenance.dirty is False
    assert len(provenance.working_tree_fingerprint) == 64
    assert resolve_repository_root(clean_repository / "src") == clean_repository.resolve()


def test_absolute_checkout_location_does_not_affect_git_or_artifact_identity(
    clean_repository: Path,
    tmp_path: Path,
) -> None:
    clone = tmp_path / "other-location" / "clone"
    clone.parent.mkdir()
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(clean_repository), str(clone)],
        capture_output=True,
        check=True,
    )
    first_git = capture_git_provenance(clean_repository)
    second_git = capture_git_provenance(clone)
    assert first_git == second_git
    first_artifact = capture_model_artifacts(
        clean_repository, {"model": clean_repository / "models" / "model.bin"}
    )
    second_artifact = capture_model_artifacts(
        clone, {"model": clone / "models" / "model.bin"}
    )
    assert first_artifact == second_artifact
    assert "repository" not in first_artifact[0].repository_relative_path
    assert "other-location" not in second_artifact[0].repository_relative_path


def test_dirty_git_state_blocks_capture_by_default(clean_repository: Path) -> None:
    (clean_repository / "src" / "model_code.py").write_text(
        "MODEL_VERSION = 'changed'\n", encoding="utf-8"
    )
    with pytest.raises(ProspectiveDirtyTreeError, match="dirty Git state"):
        capture_git_provenance(clean_repository)
    observed = capture_git_provenance(clean_repository, require_clean=False)
    assert observed.dirty is True


def test_untracked_source_and_configuration_changes_are_not_ignored(
    clean_repository: Path,
) -> None:
    (clean_repository / "new_source.py").write_text("changed = True\n", encoding="utf-8")
    (clean_repository / "settings.toml").write_text("threshold = 0.5\n", encoding="utf-8")
    with pytest.raises(ProspectiveDirtyTreeError):
        capture_git_provenance(clean_repository)


def test_only_approved_generated_directories_are_ignored(
    clean_repository: Path,
) -> None:
    generated = (
        clean_repository / "data" / "lifecycle" / "events.jsonl",
        clean_repository / "outputs" / "runtime" / "board.csv",
        clean_repository / "test_outputs" / "result.txt",
    )
    for path in generated:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("generated\n", encoding="utf-8")
    assert capture_git_provenance(clean_repository).dirty is False
    (clean_repository / "data" / "history").mkdir(parents=True)
    (clean_repository / "data" / "history" / "pick_history.csv").write_text(
        "material\n", encoding="utf-8"
    )
    with pytest.raises(ProspectiveDirtyTreeError):
        capture_git_provenance(clean_repository)


def test_unknown_git_state_blocks_capture(tmp_path: Path) -> None:
    non_repository = tmp_path / "not-a-repository"
    non_repository.mkdir()
    with pytest.raises(ProspectiveProvenanceError, match="blocked"):
        capture_git_provenance(non_repository)


def test_missing_model_artifact_fails_closed(clean_repository: Path) -> None:
    with pytest.raises(ProspectiveMissingArtifactError, match="missing"):
        capture_model_artifacts(
            clean_repository, {"model": "models/not-present.bin"}
        )


def test_model_artifacts_outside_repository_are_rejected(
    clean_repository: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    with pytest.raises(ProspectiveMissingArtifactError, match="outside"):
        capture_model_artifacts(clean_repository, {"model": outside})


def test_modified_model_contents_change_digest(clean_repository: Path) -> None:
    model = clean_repository / "models" / "model.bin"
    first = capture_model_artifacts(clean_repository, {"model": model})[0]
    model.write_bytes(b"synthetic-model-version-two")
    second = capture_model_artifacts(clean_repository, {"model": model})[0]
    assert first.sha256 != second.sha256
    assert first.size_bytes != second.size_bytes


def test_incorrect_claimed_artifact_digest_is_rejected(
    clean_repository: Path,
) -> None:
    captured = capture_model_artifacts(
        clean_repository, {"model": "models/model.bin"}
    )
    incorrect = ModelArtifactEntryV1(
        logical_name=captured[0].logical_name,
        repository_relative_path=captured[0].repository_relative_path,
        sha256="f" * 64,
        size_bytes=captured[0].size_bytes,
    )
    with pytest.raises(ProspectiveDigestMismatchError, match="model"):
        validate_model_build_manifest(_manifest((incorrect,)), clean_repository)


def test_manifest_must_represent_every_caller_required_artifact(
    clean_repository: Path,
) -> None:
    captured = capture_model_artifacts(
        clean_repository, {"model": "models/model.bin"}
    )
    with pytest.raises(ProspectiveUnverifiedModelError, match="calibration"):
        validate_model_build_manifest(
            _manifest(captured),
            clean_repository,
            required_artifact_names=("model", "calibration"),
        )


def test_unmanifested_legacy_artifact_is_not_automatically_verified(
    clean_repository: Path,
) -> None:
    legacy = clean_repository / "models" / "model.bin"
    assert legacy.is_file()
    with pytest.raises(ProspectiveUnverifiedModelError, match="legacy"):
        validate_model_build_manifest(None, clean_repository)  # type: ignore[arg-type]


def test_configuration_capture_never_reads_dotenv(
    clean_repository: Path,
) -> None:
    dotenv = clean_repository / ".env"
    dotenv.write_text("API_KEY=do-not-read-this\n", encoding="utf-8")
    before = dotenv.read_bytes()
    provenance = capture_configuration_provenance(
        {"markets": ["player_points"], "paper_trial": True}
    )
    assert dotenv.read_bytes() == before
    assert "do-not-read-this" not in str(provenance.to_dict())
    with pytest.raises(ProspectiveSecretConfigurationError):
        capture_configuration_provenance({"api_key": "do-not-read-this"})


def test_provenance_capture_performs_no_writes_or_directory_creation(
    clean_repository: Path,
) -> None:
    before = _snapshot_tree(clean_repository)
    capture_git_provenance(clean_repository)
    capture_model_artifacts(clean_repository, {"model": "models/model.bin"})
    capture_configuration_provenance({"paper_trial": True})
    after = _snapshot_tree(clean_repository)
    assert after == before


def test_runtime_and_history_files_remain_unchanged(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path / "history-repository")
    runtime_history = repository / "outputs" / "runtime" / "history" / "result.csv"
    data_history = repository / "data" / "history" / "pick_history.csv"
    runtime_history.parent.mkdir(parents=True)
    data_history.parent.mkdir(parents=True)
    runtime_history.write_bytes(b"runtime-sentinel\n")
    data_history.write_bytes(b"history-sentinel\n")
    _git(repository, "add", "-f", "outputs/runtime/history/result.csv")
    _git(repository, "add", "data/history/pick_history.csv")
    _git(repository, "commit", "--quiet", "-m", "add synthetic history sentinels")
    before = (runtime_history.read_bytes(), data_history.read_bytes())
    capture_git_provenance(repository)
    capture_model_artifacts(repository, {"model": "models/model.bin"})
    after = (runtime_history.read_bytes(), data_history.read_bytes())
    assert after == before


def test_artifact_modification_time_is_not_training_completion_evidence(
    clean_repository: Path,
) -> None:
    model = clean_repository / "models" / "model.bin"
    model.touch()
    artifact = capture_model_artifacts(clean_repository, {"model": model})[0]
    assert not hasattr(artifact, "training_completed_at_utc")
    with pytest.raises(ProspectiveUnverifiedModelError, match="training_completed_at_utc"):
        TrainingProvenanceV1(
            training_start_date=date(2024, 1, 1),
            training_end_date=date(2025, 1, 1),
            training_completed_at_utc=None,  # type: ignore[arg-type]
            training_run_id="run",
            training_data_digest="a" * 64,
            model_build_tool_version="tool-v1",
        )
