from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import urllib.request

import pytest

from scripts import create_evidence_day0_manifest as day0


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "courtvision-tests@example.invalid")
    _git(repo, "config", "user.name", "CourtVision Tests")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-m", "seed")
    return repo


def _create(clean_repo: Path, output_dir: Path, **overrides):
    kwargs = {
        "trial_id": "nba-forward-2026-01",
        "start_date": "2026-10-20",
        "end_date": "2026-11-18",
        "repo_root": clean_repo,
        "output_dir": output_dir,
        "process_env": {},
    }
    kwargs.update(overrides)
    return day0.create_evidence_day0_manifest(**kwargs)


def test_manifest_is_created_with_required_fields(clean_repo: Path, tmp_path: Path) -> None:
    path, manifest, status = _create(clean_repo, tmp_path / "manifest-output")

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == manifest
    assert status == "created"
    assert path.name == "day0_manifest_nba-forward-2026-01.json"
    assert manifest["trial_id"] == "nba-forward-2026-01"
    assert manifest["start_date"] == "2026-10-20"
    assert manifest["end_date"] == "2026-11-18"
    assert manifest["code_sha"] == _git(clean_repo, "rev-parse", "HEAD")
    assert manifest["git_branch"] == _git(clean_repo, "branch", "--show-current")
    assert manifest["git_status_short"] == ""
    assert manifest["working_tree_status"] == "clean"
    assert len(manifest["config_hash"]) == 64
    assert manifest["config_hash"] == day0.compute_config_hash(manifest["config_object"])
    assert set(day0.ENV_DEFAULTS).issubset(manifest["config_object"])
    assert set(day0.POLICY_DEFAULTS).issubset(manifest["config_object"])
    assert manifest["config_object"]["BALLDONTLIE_API_KEY"] == "missing"


def test_config_hash_is_deterministic() -> None:
    env_a = {
        "COURTVISION_MODE": "BETTING",
        "BALLDONTLIE_VENDORS": "draftkings,fanduel",
        "BALLDONTLIE_API_KEY": "first-secret",
    }
    env_b = {
        "BALLDONTLIE_API_KEY": "different-secret",
        "BALLDONTLIE_VENDORS": "fanduel,draftkings",
        "COURTVISION_MODE": "betting",
    }

    config_a = day0.resolve_config_object(env_a)
    config_b = day0.resolve_config_object(env_b)

    assert config_a == config_b
    assert day0.compute_config_hash(config_a) == day0.compute_config_hash(config_b)


def test_secret_values_are_never_written(clean_repo: Path, tmp_path: Path) -> None:
    secret = "bdl-super-secret-never-write-me"
    path, manifest, _ = _create(
        clean_repo,
        tmp_path / "manifest-output",
        process_env={"BALLDONTLIE_API_KEY": f'  "{secret}"  '},
    )

    content = path.read_text(encoding="utf-8")
    assert secret not in content
    assert manifest["config_object"]["BALLDONTLIE_API_KEY"] == "configured"


def test_existing_manifest_requires_force(clean_repo: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "manifest-output"
    path, _, _ = _create(clean_repo, output_dir)
    original = path.read_bytes()

    with pytest.raises(day0.Day0ManifestError, match="already exists"):
        _create(clean_repo, output_dir, process_env={"COURTVISION_MODE": "research"})
    assert path.read_bytes() == original

    _, overwritten, status = _create(
        clean_repo,
        output_dir,
        process_env={"COURTVISION_MODE": "research"},
        force=True,
    )
    assert status == "overwritten"
    assert overwritten["config_object"]["COURTVISION_MODE"] == "research"
    assert path.read_bytes() != original


def test_dirty_working_tree_fails_safely(clean_repo: Path, tmp_path: Path) -> None:
    (clean_repo / "seed.txt").write_text("dirty\n", encoding="utf-8")
    output_dir = tmp_path / "manifest-output"

    with pytest.raises(day0.DirtyWorkingTreeError, match="working tree is not clean"):
        _create(clean_repo, output_dir)

    assert not output_dir.exists()


def test_ignored_files_do_not_make_tree_dirty(clean_repo: Path, tmp_path: Path) -> None:
    (clean_repo / ".gitignore").write_text("ignored-runtime.txt\n", encoding="utf-8")
    _git(clean_repo, "add", ".gitignore")
    _git(clean_repo, "commit", "-m", "ignore runtime file")
    (clean_repo / "ignored-runtime.txt").write_text("runtime\n", encoding="utf-8")

    path, manifest, _ = _create(clean_repo, tmp_path / "manifest-output")

    assert path.is_file()
    assert manifest["git_status_short"] == ""


def test_untracked_investor_audit_requires_explicit_flag(
    clean_repo: Path, tmp_path: Path
) -> None:
    audit_path = clean_repo / day0.ALLOWED_INVESTOR_AUDIT
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text("known audit\n", encoding="utf-8")

    with pytest.raises(day0.DirtyWorkingTreeError):
        _create(clean_repo, tmp_path / "blocked-output")

    path, manifest, _ = _create(
        clean_repo,
        tmp_path / "allowed-output",
        allow_untracked_investor_audit=True,
    )
    assert path.is_file()
    assert manifest["git_status_short"] == f"?? {day0.ALLOWED_INVESTOR_AUDIT}"
    assert manifest["working_tree_status"] == "clean_with_allowed_untracked_investor_audit"


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        ("2026-02-30", "2026-03-01"),
        ("2026/02/01", "2026-03-01"),
        ("2026-03-02", "2026-03-01"),
    ],
)
def test_invalid_dates_fail_safely(
    clean_repo: Path, tmp_path: Path, start_date: str, end_date: str
) -> None:
    output_dir = tmp_path / "manifest-output"
    with pytest.raises(day0.Day0ManifestError):
        _create(clean_repo, output_dir, start_date=start_date, end_date=end_date)
    assert not output_dir.exists()


def test_script_is_offline_safe(
    clean_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("Day 0 manifest creation must not use the network")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)

    path, _, _ = _create(clean_repo, tmp_path / "manifest-output")
    assert path.is_file()
