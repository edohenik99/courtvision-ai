from __future__ import annotations

import json
from pathlib import Path
import sys
import tomllib

import pytest

from courtvision.cli.main import build_parser, main


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _tree_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_console_script_is_declared() -> None:
    payload = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert payload["project"]["scripts"]["courtvision"] == "courtvision.cli.main:main"


def test_help_works(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "doctor" in output
    assert "collect" in output
    assert "version" in output


def test_doctor_works(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor", "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["sports_data_fetched"] is False
    assert report["writes_performed"] is False


def test_version_works(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0

    assert capsys.readouterr().out.strip().startswith("courtvision ")


def test_collect_mlb_dry_run_does_not_fetch_or_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_statcast(*args: object, **kwargs: object) -> None:
        raise AssertionError("Statcast must not be called during a CLI dry-run")

    def fail_chadwick(*args: object, **kwargs: object) -> None:
        raise AssertionError("Chadwick must not be called during a CLI dry-run")

    fake_pybaseball = type(
        "FakePybaseball", (), {"statcast": staticmethod(fail_statcast)}
    )()
    monkeypatch.setitem(sys.modules, "pybaseball", fake_pybaseball)
    monkeypatch.setattr("requests.get", fail_chadwick)
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "collect",
                "mlb",
                "--season",
                "2025",
                "--fetch-statcast",
                "--fetch-chadwick-register",
                "--dry-run",
            ]
        )
        == 0
    )

    report = json.loads(capsys.readouterr().out)
    assert report["sport"] == "mlb"
    assert report["dry_run"] is True
    assert report["writes_performed"] is False
    assert "statcast_pybaseball" in report["planned_sources"]
    assert "chadwick_bureau_register" in report["planned_sources"]
    assert not (tmp_path / "courtvision-raw").exists()


def test_statcast_resume_and_chunk_size_flags_are_parsed() -> None:
    args = build_parser().parse_args(
        [
            "collect",
            "mlb",
            "--season",
            "2025",
            "--collection-id",
            "v2025-resume",
            "--fetch-statcast",
            "--resume",
            "--statcast-chunk-size",
            "biweekly",
        ]
    )

    assert args.resume is True
    assert args.statcast_chunk_size == "biweekly"


def test_resume_requires_fetch_statcast_and_explicit_collection_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["collect", "mlb", "--season", "2025", "--resume"])

    assert exc_info.value.code == 2
    assert "--resume requires --fetch-statcast" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "collect",
                "mlb",
                "--season",
                "2025",
                "--fetch-statcast",
                "--resume",
            ]
        )

    assert exc_info.value.code == 2
    assert "--resume requires --collection-id" in capsys.readouterr().err


@pytest.mark.parametrize("sport", ("nba", "nfl", "nhl", "wnba"))
def test_unsupported_sports_fail_closed_with_clear_message(
    sport: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["collect", sport, "--season", "2025", "--dry-run"]) == 2

    error = capsys.readouterr().err
    assert f"failed closed for {sport}" in error
    assert "registry stub" in error
    assert not (tmp_path / "courtvision-raw").exists()


def test_safe_commands_do_not_mutate_protected_folders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for folder in ("outputs", "history", "runtime", "cache", "manual-data"):
        path = tmp_path / folder
        path.mkdir()
        (path / "sentinel.txt").write_text(folder, encoding="utf-8")
    before = _tree_snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    assert main(["doctor"]) == 0
    assert main(["collect", "mlb", "--season", "2025", "--dry-run"]) == 0
    capsys.readouterr()

    assert _tree_snapshot(tmp_path) == before
    assert not (tmp_path / "courtvision-raw").exists()
