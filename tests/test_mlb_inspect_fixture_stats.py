from __future__ import annotations

import json
from pathlib import Path
import socket
import urllib.request

import scripts.mlb_inspect_fixture_stats as inspection


EXPECTED_COUNTS = (
    "Statcast row count: 2",
    "Retrosheet game count: 4",
    "Retrosheet event count: 2",
    "Weather row count: 3",
    "Ballpark row count: 3",
    "HR batter-game dataset row count: 4",
    "Leakage audit summary: rows=4, errors=0",
)


def _fixture_files() -> tuple[str, ...]:
    return tuple(
        sorted(
            str(path.relative_to(inspection.FIXTURE_DIR))
            for path in inspection.FIXTURE_DIR.rglob("*")
            if path.is_file()
        )
    )


def test_script_runs_keylessly_and_prints_expected_counts(
    monkeypatch, capsys
) -> None:
    for name in ("ODDS_API_KEY", "THE_ODDS_API_KEY", "MLB_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    assert inspection.main() == 0

    output = capsys.readouterr().out
    assert "historical research only" in output
    for expected in EXPECTED_COUNTS:
        assert expected in output
    assert "warnings=" in output
    assert "passed=true" in output

    row_lines = [line for line in output.splitlines() if line.startswith("{")]
    assert len(row_lines) == 4
    first_row = json.loads(row_lines[0])
    assert tuple(first_row) == inspection.INSPECTION_FIELDS
    assert first_row == {
        "player_name": "Sample Slugger",
        "player_id": "b001",
        "game_id": "20250401TORBOS-1",
        "game_date": "2025-04-01",
        "team": "TOR",
        "opponent": "BOS",
        "venue_name": "Rogers Centre",
        "hit_hr_today": True,
        "home_run_count": 1,
        "weather_temperature": 72.0,
        "weather_wind_speed": 9.5,
        "park_factor_hr": 1.08,
        "missing_required_fields": [],
        "warnings": [],
        "eligible_for_training": True,
        "approval_status": "not_approved",
    }

    assert first_row["weather_temperature"] == 72.0
    assert any(
        row["weather_temperature"] is None or row["park_factor_hr"] is None
        for row in map(json.loads, row_lines)
    )


def test_script_uses_only_repository_fixture_paths(monkeypatch, capsys) -> None:
    observed_paths: list[Path] = []

    def record_call(callable_):
        def wrapper(*args, **kwargs):
            observed_paths.extend(
                Path(value).resolve()
                for value in (*args, *kwargs.values())
                if isinstance(value, (str, Path))
            )
            return callable_(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(
        inspection,
        "ingest_local_statcast_csv",
        record_call(inspection.ingest_local_statcast_csv),
    )
    monkeypatch.setattr(
        inspection,
        "ingest_local_retrosheet_csvs",
        record_call(inspection.ingest_local_retrosheet_csvs),
    )
    monkeypatch.setattr(
        inspection,
        "ingest_local_weather_csv",
        record_call(inspection.ingest_local_weather_csv),
    )
    monkeypatch.setattr(
        inspection,
        "ingest_local_ballpark_factors_csv",
        record_call(inspection.ingest_local_ballpark_factors_csv),
    )
    monkeypatch.setattr(
        inspection,
        "build_fixture_hr_batter_game_dataset",
        record_call(inspection.build_fixture_hr_batter_game_dataset),
    )

    assert inspection.main() == 0
    capsys.readouterr()

    assert observed_paths
    assert all(
        path == inspection.FIXTURE_DIR
        or inspection.FIXTURE_DIR in path.parents
        for path in observed_paths
    )


def test_script_does_not_create_files(monkeypatch, tmp_path, capsys) -> None:
    fixture_files_before = _fixture_files()
    monkeypatch.chdir(tmp_path)

    assert inspection.main() == 0
    capsys.readouterr()

    assert not tuple(tmp_path.rglob("*"))
    assert _fixture_files() == fixture_files_before


def test_script_does_not_call_network(monkeypatch, capsys) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("network access is not allowed")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)

    assert inspection.main() == 0
    capsys.readouterr()
