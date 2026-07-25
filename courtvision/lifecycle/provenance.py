"""Best-effort, secret-free code/model/config provenance capture."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import importlib.metadata
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping

from courtvision.lifecycle.canonical import file_sha256, payload_sha256
from courtvision.lifecycle.evidence import sanitize_evidence


CommandRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def capture_git_provenance(
    repository_root: Path,
    *,
    command_runner: CommandRunner = _run,
) -> dict[str, Any]:
    root = Path(repository_root)
    try:
        sha_result = command_runner(["git", "rev-parse", "HEAD"], root)
        status_result = command_runner(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
                "--",
                ".",
                ":(exclude)data/lifecycle",
                ":(exclude)outputs",
                ":(exclude)test_outputs",
            ],
            root,
        )
        diff_result = command_runner(
            [
                "git",
                "diff",
                "--binary",
                "--no-ext-diff",
                "HEAD",
                "--",
                ".",
                ":(exclude)data/lifecycle",
                ":(exclude)outputs",
                ":(exclude)test_outputs",
            ],
            root,
        )
    except (OSError, subprocess.SubprocessError):
        return {
            "git_commit_sha": None,
            "git_dirty": None,
            "working_tree_hash": None,
            "working_tree_fingerprint_scope": "unavailable",
        }
    sha = sha_result.stdout.strip() if sha_result.returncode == 0 else None
    if status_result.returncode != 0:
        dirty: bool | None = None
    else:
        dirty = bool(status_result.stdout.strip())
    working_tree_hash = None
    if diff_result.returncode == 0 and dirty:
        working_tree_hash = payload_sha256(
            {
                "scope": "tracked git diff HEAD excluding data/lifecycle, outputs, test_outputs",
                "diff": diff_result.stdout,
            }
        )
    elif diff_result.returncode == 0 and dirty is False:
        working_tree_hash = payload_sha256(
            {
                "scope": "tracked git diff HEAD excluding data/lifecycle, outputs, test_outputs",
                "diff": "",
            }
        )
    return {
        "git_commit_sha": sha,
        "git_dirty": dirty,
        "working_tree_hash": working_tree_hash,
        "working_tree_fingerprint_scope": (
            "tracked git diff HEAD excluding data/lifecycle, outputs, test_outputs"
            if diff_result.returncode == 0
            else "unavailable"
        ),
    }


def dependency_fingerprint() -> str | None:
    try:
        distributions = sorted(
            {
                (
                    (dist.metadata.get("Name") or "").strip().lower(),
                    dist.version,
                )
                for dist in importlib.metadata.distributions()
                if (dist.metadata.get("Name") or "").strip()
            }
        )
    except Exception:
        return None
    return payload_sha256(
        {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "distributions": [list(item) for item in distributions],
        }
    )


def model_artifact_manifest(paths: Iterable[Path]) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for path in paths:
        candidate = Path(path)
        artifacts.append(
            {
                "name": candidate.name,
                "exists": candidate.is_file(),
                "sha256": file_sha256(candidate) if candidate.is_file() else None,
                "size_bytes": candidate.stat().st_size if candidate.is_file() else None,
            }
        )
    return {"artifacts": artifacts}


def safe_runtime_config_snapshot(
    runtime: Any,
    *,
    prediction_date: str,
    verbose_outputs: bool,
    force_output_overwrite: bool,
) -> dict[str, Any]:
    policies: dict[str, Any] = {}
    for name in (
        "board_scoring",
        "player_selection",
        "qualification_gate",
        "board_volume",
    ):
        policy = getattr(runtime, name, None)
        config = getattr(policy, "config", None)
        if config is None:
            config = getattr(policy, "_config", None)
        if config is None:
            continue
        if is_dataclass(config):
            policies[name] = asdict(config)
        elif isinstance(config, Mapping):
            policies[name] = dict(config)
        else:
            policies[name] = {
                "type": f"{type(config).__module__}.{type(config).__qualname__}",
                "values_unavailable": True,
            }
    return sanitize_evidence(
        {
            "prediction_date": prediction_date,
            "verbose_outputs": bool(verbose_outputs),
            "force_output_overwrite": bool(force_output_overwrite),
            "policies": policies,
        }
    )


def python_version() -> str:
    return sys.version.splitlines()[0]


__all__ = [
    "capture_git_provenance",
    "dependency_fingerprint",
    "model_artifact_manifest",
    "python_version",
    "safe_runtime_config_snapshot",
]
