from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import urllib.request

import pytest

from courtvision.sports.mlb.data import weather_ingestion
from courtvision.sports.mlb.data.weather_ingestion import (
    WeatherIngestionError,
    ingest_local_weather_csv,
    weather_row_to_context,
    weather_row_to_dict,
    weather_row_to_json,
)
from courtvision.sports.mlb.data_manifest import validate_source_manifest
from courtvision.sports.mlb.research_context import MLBWeatherContext
from scripts.mlb_ingest_weather import main


FIXTURE = Path(__file__).parent / "fixtures" / "mlb" / "weather_sample.csv"
FORBIDDEN_FIELDS = {
    "ev",
    "fair_probability",
    "unit_size",
    "staking",
    "kelly",
    "bankroll",
    "elite",
    "strong",
    "recommendation",
    "production",
}


def test_weather_fixture_parses_as_historical_observations() -> None:
    result = ingest_local_weather_csv(FIXTURE)

    assert len(result.rows) == 3
    assert result.rows[0].sport == "MLB"
    assert result.rows[0].source == "weather_historical_fixture"
    assert result.rows[0].source_type == "historical"
    assert "not a pregame forecast" in result.rows[0].warnings[0]


def test_required_columns_are_enforced(tmp_path: Path) -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    headers = lines[0].split(",")
    remove_index = headers.index("venue_name")
    rewritten = []
    for line in lines:
        values = line.split(",")
        rewritten.append(",".join(values[:remove_index] + values[remove_index + 1 :]))
    path = tmp_path / "missing-column.csv"
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    with pytest.raises(WeatherIngestionError, match="missing required columns"):
        ingest_local_weather_csv(path)


def test_invalid_game_date_fails_with_row_context(tmp_path: Path) -> None:
    path = tmp_path / "invalid-date.csv"
    path.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(
            "2025-04-01", "04/01/2025", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(WeatherIngestionError, match=r"row 2: game_date"):
        ingest_local_weather_csv(path)


def test_invalid_collected_at_fails_with_row_context(tmp_path: Path) -> None:
    path = tmp_path / "invalid-collected-at.csv"
    path.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(
            "2026-06-19T16:00:00Z", "not-a-timestamp", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(WeatherIngestionError, match=r"row 2: collected_at"):
        ingest_local_weather_csv(path)


def test_empty_numeric_values_stay_none_and_mark_incomplete() -> None:
    row = ingest_local_weather_csv(FIXTURE).rows[2]

    assert row.temperature is None
    assert row.wind_speed is None
    assert row.humidity is None
    assert row.data_quality == "incomplete_historical"
    assert "Missing weather field: temperature." in row.warnings
    assert "Missing weather field: wind_speed." in row.warnings


def test_unknown_roof_status_is_preserved_without_guessing() -> None:
    row = ingest_local_weather_csv(FIXTURE).rows[1]

    assert row.roof_status == "unknown"
    assert weather_row_to_context(row).roof_status == "unknown"


def test_serialization_is_deterministic_and_has_no_decision_fields() -> None:
    row = ingest_local_weather_csv(FIXTURE).rows[0]

    assert weather_row_to_json(row) == weather_row_to_json(row)
    assert json.loads(weather_row_to_json(row)) == weather_row_to_dict(row)
    assert FORBIDDEN_FIELDS.isdisjoint(weather_row_to_dict(row))


def test_weather_row_maps_to_existing_research_context() -> None:
    row = ingest_local_weather_csv(FIXTURE).rows[0]

    context = weather_row_to_context(row)

    assert isinstance(context, MLBWeatherContext)
    assert context.game_id == row.game_id
    assert context.temperature == row.temperature
    assert context.wind_speed == row.wind_speed
    assert context.source_type == "historical"
    assert context.data_quality == "complete_historical"
    assert context.mode == "research"


def test_missing_game_id_maps_without_inventing_identity() -> None:
    row = ingest_local_weather_csv(FIXTURE).rows[2]

    context = weather_row_to_context(row)

    assert row.game_id is None
    assert context.game_id == ""
    assert any("game_id is missing" in warning for warning in context.warnings)


def test_manifest_uses_phase3b_weather_contract_and_checksum() -> None:
    manifest = ingest_local_weather_csv(FIXTURE).manifest

    assert validate_source_manifest(manifest).is_valid
    assert manifest.source_name == "weather_historical_fixture"
    assert manifest.source_type == "historical"
    assert manifest.data_domain == "weather"
    assert manifest.date_range_start == date(2025, 4, 1)
    assert manifest.date_range_end == date(2025, 4, 3)
    assert manifest.as_of_date == date(2026, 6, 19)
    assert manifest.row_count == 3
    assert manifest.file_count == 1
    assert manifest.checksum == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert manifest.files[0].checksum == manifest.checksum


def test_manifest_calls_phase3b_checksum_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    real_helper = weather_ingestion.compute_file_sha256

    def recording_helper(path: str | Path) -> str:
        calls.append(Path(path))
        return real_helper(path)

    monkeypatch.setattr(weather_ingestion, "compute_file_sha256", recording_helper)

    ingest_local_weather_csv(FIXTURE)

    assert calls == [FIXTURE.resolve()]


def test_dry_run_api_and_cli_create_no_output_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "dry-run-output"

    result = ingest_local_weather_csv(FIXTURE, output_dir=output_dir)
    exit_code = main(
        [
            "--input-csv",
            str(FIXTURE),
            "--out-dir",
            str(output_dir),
            "--dry-run",
        ]
    )

    assert result.raw_output_path is None
    assert result.normalized_output_path is None
    assert result.manifest_output_path is None
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "dry-run"
    assert not output_dir.exists()


def test_explicit_outputs_are_confined_to_temp_directory(tmp_path: Path) -> None:
    output_dir = tmp_path / "explicit-output"

    result = ingest_local_weather_csv(
        FIXTURE,
        output_dir=output_dir,
        write_raw=True,
        write_normalized=True,
        write_manifest_file=True,
    )

    paths = (
        result.raw_output_path,
        result.normalized_output_path,
        result.manifest_output_path,
    )
    assert all(path is not None and path.is_file() for path in paths)
    assert all(
        output_dir.resolve() in path.parents for path in paths if path is not None
    )
    assert result.raw_output_path.read_bytes() == FIXTURE.read_bytes()
    assert len(
        result.normalized_output_path.read_text(encoding="utf-8").splitlines()
    ) == 3


def test_local_ingestion_has_no_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fail_if_called(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("network should not be called")

    monkeypatch.setattr(urllib.request, "urlopen", fail_if_called)

    ingest_local_weather_csv(FIXTURE)

    assert called is False
