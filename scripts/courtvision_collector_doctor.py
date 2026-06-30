"""Read-only dependency doctor and explicit bootstrap for approved collectors."""

from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]

APPROVED_DISTRIBUTIONS = (
    "meteostat",
    "pandas",
    "pybaseball",
    "python-dateutil",
    "requests",
)
APPROVED_INSTALL_PACKAGES = {
    "collector-mlb": ("pandas", "pybaseball", "python-dateutil", "requests"),
    "collector-weather": ("meteostat", "pandas", "python-dateutil", "requests"),
    "collector-all": APPROVED_DISTRIBUTIONS,
}
APPROVED_INSTALL_GROUPS = tuple(APPROVED_INSTALL_PACKAGES)
FEATURE_REQUIREMENTS = {
    "statcast": ("pybaseball", "pandas", "python-dateutil", "requests"),
    "weather": ("meteostat", "pandas", "python-dateutil", "requests"),
}

VersionLookup = Callable[[str], str]
CommandRunner = Callable[..., subprocess.CompletedProcess[object]]


def inspect_dependencies(
    version_lookup: VersionLookup | None = None,
) -> dict[str, str | None]:
    """Return installed versions without importing collector packages."""

    lookup = version_lookup or metadata.version
    versions: dict[str, str | None] = {}
    for distribution in APPROVED_DISTRIBUTIONS:
        try:
            versions[distribution] = lookup(distribution)
        except metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions


def build_doctor_report(
    version_lookup: VersionLookup | None = None,
) -> dict[str, object]:
    versions = inspect_dependencies(version_lookup)
    dependencies = {
        name: {"installed": version is not None, "version": version}
        for name, version in versions.items()
    }
    features: dict[str, dict[str, object]] = {}
    for name, requirements in FEATURE_REQUIREMENTS.items():
        missing = [
            requirement for requirement in requirements if versions[requirement] is None
        ]
        features[name] = {
            "available": not missing,
            "missing": missing,
            "requires": list(requirements),
        }
    return {
        "availability_scope": "installed_dependencies_only",
        "dependencies": dependencies,
        "features": features,
        "sports_data_fetched": False,
        "writes_performed": False,
    }


def format_report(report: Mapping[str, object]) -> str:
    dependencies = report["dependencies"]
    features = report["features"]
    assert isinstance(dependencies, Mapping)
    assert isinstance(features, Mapping)

    lines = ["CourtVision collector dependency doctor", "", "Dependencies:"]
    for name in APPROVED_DISTRIBUTIONS:
        detail = dependencies[name]
        assert isinstance(detail, Mapping)
        version = detail["version"]
        status = f"installed ({version})" if version is not None else "MISSING"
        lines.append(f"  {name}: {status}")

    lines.extend(("", "Collector dependency readiness:"))
    for name in FEATURE_REQUIREMENTS:
        detail = features[name]
        assert isinstance(detail, Mapping)
        missing = detail["missing"]
        assert isinstance(missing, list)
        status = "AVAILABLE" if detail["available"] else "UNAVAILABLE"
        suffix = "" if not missing else f" (missing: {', '.join(missing)})"
        lines.append(f"  {name}: {status}{suffix}")

    lines.extend(
        (
            "",
            "Safety:",
            "  sports data fetched: no",
            "  files or operational folders written: no",
        )
    )
    return "\n".join(lines)


def install_dependency_group(
    group: str,
    command_runner: CommandRunner | None = None,
) -> int:
    """Run pip only for an explicitly selected, allowlisted package group."""

    if group not in APPROVED_INSTALL_PACKAGES:
        raise ValueError(f"unsupported collector dependency group: {group}")
    packages = APPROVED_INSTALL_PACKAGES[group]

    runner = command_runner or subprocess.run
    command = [
        sys.executable,
        "-m",
        "pip",
        "--disable-pip-version-check",
        "install",
        *packages,
    ]
    print(f"Installing approved dependency group: {group}")
    completed = runner(command, cwd=PROJECT_ROOT, check=False)
    return int(completed.returncode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check approved CourtVision collector dependencies without importing "
            "collectors, fetching sports data, or writing files."
        )
    )
    parser.add_argument(
        "--install",
        choices=APPROVED_INSTALL_GROUPS,
        help="Explicitly install one allowlisted collector dependency group.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the read-only dependency report as JSON.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    version_lookup: VersionLookup | None = None,
    command_runner: CommandRunner | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if args.install:
        return install_dependency_group(args.install, command_runner)

    report = build_doctor_report(version_lookup)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
