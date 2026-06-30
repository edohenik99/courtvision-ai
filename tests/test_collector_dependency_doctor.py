from __future__ import annotations

from importlib import metadata
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import courtvision_collector_doctor as doctor


ALL_INSTALLED = {
    "meteostat": "2.0.0",
    "pandas": "2.3.0",
    "pybaseball": "2.2.7",
    "python-dateutil": "2.9.0.post0",
    "requests": "2.32.0",
}


def _lookup(installed: dict[str, str]):
    def lookup(name: str) -> str:
        try:
            return installed[name]
        except KeyError as exc:
            raise metadata.PackageNotFoundError(name) from exc

    return lookup


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def test_missing_pybaseball_reports_statcast_unavailable() -> None:
    installed = {
        name: version
        for name, version in ALL_INSTALLED.items()
        if name != "pybaseball"
    }

    report = doctor.build_doctor_report(_lookup(installed))

    assert report["features"]["statcast"]["available"] is False
    assert report["features"]["statcast"]["missing"] == ["pybaseball"]


def test_missing_meteostat_reports_weather_unavailable() -> None:
    installed = {
        name: version
        for name, version in ALL_INSTALLED.items()
        if name != "meteostat"
    }

    report = doctor.build_doctor_report(_lookup(installed))

    assert report["features"]["weather"]["available"] is False
    assert report["features"]["weather"]["missing"] == ["meteostat"]


def test_installed_dependencies_report_features_available() -> None:
    report = doctor.build_doctor_report(_lookup(ALL_INSTALLED))

    assert report["features"]["statcast"]["available"] is True
    assert report["features"]["weather"]["available"] is True
    assert report["dependencies"]["pybaseball"]["version"] == "2.2.7"


def test_doctor_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for folder in ("outputs", "history", "runtime", "manual", "cache"):
        path = tmp_path / folder
        path.mkdir()
        (path / "sentinel.txt").write_text(folder, encoding="utf-8")
    before = _tree_snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert doctor.main([], version_lookup=_lookup(ALL_INSTALLED)) == 0

    assert _tree_snapshot(tmp_path) == before


def test_unsupported_package_install_is_rejected_before_pip_runs() -> None:
    def fail_if_called(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        raise AssertionError("pip must not run for an unsupported package")

    with pytest.raises(ValueError, match="unsupported collector dependency group"):
        doctor.install_dependency_group("numpy", fail_if_called)

    with pytest.raises(SystemExit) as exc_info:
        doctor.main(["--install", "numpy"], command_runner=fail_if_called)
    assert exc_info.value.code == 2


def test_dependency_check_does_not_fetch_sports_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_statcast(*args: object, **kwargs: object) -> None:
        raise AssertionError("Statcast must not be called by dependency checks")

    fake_pybaseball = type(
        "FakePybaseball", (), {"statcast": staticmethod(fail_statcast)}
    )()
    monkeypatch.setitem(sys.modules, "pybaseball", fake_pybaseball)

    report = doctor.build_doctor_report(_lookup(ALL_INSTALLED))

    assert report["sports_data_fetched"] is False


def test_allowlisted_install_uses_project_extra() -> None:
    calls: list[tuple[list[str], Path, bool]] = []

    def runner(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[object]:
        calls.append((command, cwd, check))
        return subprocess.CompletedProcess(command, 0)

    assert doctor.install_dependency_group("collector-mlb", runner) == 0
    assert calls == [
        (
            [
                doctor.sys.executable,
                "-m",
                "pip",
                "--disable-pip-version-check",
                "install",
                ".[collector-mlb]",
            ],
            doctor.PROJECT_ROOT,
            False,
        )
    ]
