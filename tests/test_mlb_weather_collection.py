from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.cli.main import main as cli_main
from courtvision.data_collection.core import CollectionError, CollectionRequest, collect_sources
from courtvision.data_collection.path_guards import ProtectedPathError
from courtvision.sports.mlb.data_collection.weather_collector import (
    WEATHER_DIAGNOSTICS_FILENAME,
    WEATHER_FILENAME,
    WEATHER_MISSING_REPORT_FILENAME,
)


COLLECTED_AT = datetime(2026, 6, 30, 15, 0, tzinfo=timezone.utc)


def _legacy_log(path: Path) -> Path:
    rows = [
        [
            "20250401", "0", "Tue", "BOS", "AL", "1", "NYA", "AL", "1",
            "3", "2", "54", "N", "", "", "", "NYC21",
        ],
        [
            "20250402", "0", "Wed", "BOS", "AL", "2", "NYA", "AL", "2",
            "1", "4", "54", "D", "", "", "", "NYC21",
        ],
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows)
    return path


def _headered_log(path: Path) -> Path:
    path.write_text(
        "gid,date,hometeam,visteam,site,number,starttime,daynight\n"
        "NYA202504010,2025-04-01,NYA,BOS,NYC21,0,7:05PM,night\n",
        encoding="utf-8",
    )
    return path


def _stadium_map(
    path: Path,
    *,
    park_id: str = "NYC21",
    latitude: str = "40.8296",
    longitude: str = "-73.9262",
    timezone_name: str = "America/New_York",
    roof_type: str = "unknown",
) -> Path:
    path.write_text(
        "park_id,stadium_name,latitude,longitude,timezone,elevation_m,roof_type\n"
        f"{park_id},Yankee Stadium,{latitude},{longitude},{timezone_name},17,{roof_type}\n",
        encoding="utf-8",
    )
    return path


def _request(
    tmp_path: Path,
    game_log: Path,
    stadium_map: Path,
    *,
    dry_run: bool = False,
) -> CollectionRequest:
    return CollectionRequest(
        sport="mlb",
        season=2025,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 2),
        output_raw_dir=tmp_path / "raw",
        dry_run=dry_run,
        collection_id="v2025-weather-test",
        collection_timestamp=COLLECTED_AT,
        source_options={
            "retrosheet_path": game_log,
            "fetch_weather": True,
            "weather_provider": "meteostat",
            "stadium_map_path": stadium_map,
        },
    )


class _HourlyResult:
    def __init__(self, start: datetime) -> None:
        self.start = start

    def fetch(self) -> pd.DataFrame:
        index = pd.DatetimeIndex(
            [self.start, self.start.replace(hour=self.start.hour + 1)], name="time"
        )
        return pd.DataFrame(
            {
                "temp": [12.5, 13.0],
                "rhum": [60, 58],
                "wspd": [9.2, 10.1],
                "wdir": [180, 190],
                "prcp": [0.0, 0.2],
                "pres": [1012.4, 1012.0],
            },
            index=index,
        )


def _station_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {"name": ["LaGuardia Airport"], "distance": [8123.0]},
        index=pd.Index(["72503"], name="id"),
    )


@pytest.fixture(autouse=True)
def _mock_station_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("meteostat.stations.nearby", lambda point: _station_frame())


def _weather_manifest_source(result, filename: str):
    assert result.manifest is not None
    return next(
        source
        for source in result.manifest.sources
        if source.source_name == "weather_meteostat"
        and Path(source.local_file_path).name == filename
    )


def _diagnostic_rows(result) -> list[dict[str, str]]:
    source = _weather_manifest_source(result, WEATHER_DIAGNOSTICS_FILENAME)
    path = result.collection_dir / source.local_file_path
    return list(csv.DictReader(path.open(encoding="utf-8")))


def test_fetch_weather_writes_hourly_raw_csv_and_manifest_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_log = _legacy_log(tmp_path / "gl2025.txt")
    stadium_map = _stadium_map(tmp_path / "stadiums.csv")
    calls: list[tuple[object, datetime, datetime, str | None]] = []

    def fake_hourly(point, start, end, timezone=None):
        calls.append((point, start, end, timezone))
        return _HourlyResult(start)

    monkeypatch.setattr("meteostat.hourly", fake_hourly)
    result = collect_sources(_request(tmp_path, game_log, stadium_map))

    assert len(calls) == 2
    assert calls[0][1] == datetime(2025, 4, 1, 17, 0)
    assert calls[0][2] == datetime(2025, 4, 1, 21, 0)
    assert calls[1][1] == datetime(2025, 4, 2, 11, 0)
    assert calls[0][3] == "America/New_York"
    assert result.manifest is not None
    assert result.manifest.collector_version == "1.3.1"

    weather = _weather_manifest_source(result, WEATHER_FILENAME)
    raw_path = result.collection_dir / weather.local_file_path
    assert raw_path.name == WEATHER_FILENAME
    assert weather.row_count == 4
    assert weather.sha256 == hashlib.sha256(raw_path.read_bytes()).hexdigest()
    assert "day games used 13:00 local" in " ".join(weather.warnings)

    rows = list(csv.DictReader(raw_path.open(encoding="utf-8")))
    assert rows[0]["temperature"] == "12.5"
    assert rows[0]["humidity"] == "60.0"
    assert rows[0]["wind_speed"] == "9.2"
    assert rows[0]["wind_direction"] == "180.0"
    assert rows[0]["precipitation"] == "0.0"
    assert rows[0]["pressure"] == "1012.4"
    assert rows[0]["game_time_basis"] == "retrosheet_night_or_unknown_default_19:00"

    diagnostics = _diagnostic_rows(result)
    assert [row["status"] for row in diagnostics] == ["weather_found", "weather_found"]
    assert diagnostics[0]["nearest_station_id"] == "72503"
    assert diagnostics[0]["nearest_station_name"] == "LaGuardia Airport"
    assert diagnostics[0]["station_distance_km"] == "8.123"
    assert diagnostics[0]["stations_found_count"] == "1"
    assert diagnostics[0]["hourly_rows_found_count"] == "2"
    assert diagnostics[0]["local_lookup_time"] == "2025-04-01T19:00:00-04:00"
    assert diagnostics[0]["utc_lookup_time"] == "2025-04-01T23:00:00+00:00"

    payload = json.loads(
        (result.collection_dir / "collection_manifest.json").read_text(encoding="utf-8")
    )
    weather_records = [
        source for source in payload["sources"] if source["source_name"] == "weather_meteostat"
    ]
    by_file = {Path(source["local_file_path"]).name: source for source in weather_records}
    assert by_file[WEATHER_FILENAME]["provider"] == "https://meteostat.net/"
    assert by_file[WEATHER_FILENAME]["source_notes"]
    diagnostic_record = by_file[WEATHER_DIAGNOSTICS_FILENAME]
    metadata = diagnostic_record["metadata"]
    assert metadata["weather_diagnostics_sha256"] == diagnostic_record["sha256"]
    assert metadata["missing_weather_count"] == 0
    assert metadata["missing_weather_rate"] == 0.0
    assert metadata["reason_counts"] == {}
    assert set(by_file) == {
        WEATHER_FILENAME,
        WEATHER_DIAGNOSTICS_FILENAME,
        WEATHER_MISSING_REPORT_FILENAME,
    }
    by_name = {source["source_name"]: source for source in payload["sources"]}
    assert by_name["approved_stadium_coordinates"]["row_count"] == 1
    assert payload["date_range"] == {"start": "2025-04-01", "end": "2025-04-02"}


def test_headered_retrosheet_starttime_is_used_without_approximation_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_log = _headered_log(tmp_path / "gameinfo.csv")
    stadium_map = _stadium_map(tmp_path / "stadiums.csv")
    calls: list[tuple[datetime, datetime]] = []

    def fake_hourly(point, start, end, timezone=None):
        calls.append((start, end))
        return _HourlyResult(start)

    monkeypatch.setattr("meteostat.hourly", fake_hourly)
    request = _request(tmp_path, game_log, stadium_map)
    request = CollectionRequest(
        sport=request.sport,
        season=request.season,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 1),
        output_raw_dir=request.output_raw_dir,
        collection_id=request.collection_id,
        collection_timestamp=request.collection_timestamp,
        source_options=request.source_options,
    )
    result = collect_sources(request)

    assert calls == [(datetime(2025, 4, 1, 17, 5), datetime(2025, 4, 1, 21, 5))]
    assert result.manifest is not None
    weather = _weather_manifest_source(result, WEATHER_FILENAME)
    assert not any("legacy game logs" in warning for warning in weather.warnings)
    row = next(
        csv.DictReader(
            (result.collection_dir / weather.local_file_path).open(encoding="utf-8")
        )
    )
    assert row["game_time_local"] == "2025-04-01T19:05:00-04:00"
    assert row["game_time_basis"] == "retrosheet_starttime"


def test_missing_stadium_mapping_is_blocker_and_never_calls_meteostat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_log = _legacy_log(tmp_path / "gl2025.txt")
    stadium_map = _stadium_map(tmp_path / "stadiums.csv", park_id="BOS07")

    def fail_hourly(*args, **kwargs):
        raise AssertionError("Meteostat must not run with a missing park mapping")

    monkeypatch.setattr("meteostat.hourly", fail_hourly)
    dry_request = _request(tmp_path, game_log, stadium_map, dry_run=True)
    dry_result = collect_sources(dry_request)

    assert any("NYC21" in blocker for blocker in dry_result.blockers)
    assert not dry_request.output_raw_dir.exists()

    with pytest.raises(CollectionError, match="missing stadium mapping.*NYC21"):
        collect_sources(_request(tmp_path, game_log, stadium_map))
    assert not (tmp_path / "raw").exists()


def test_cli_dry_run_plans_weather_without_fetching_or_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    game_log = _legacy_log(tmp_path / "gl2025.txt")
    stadium_map = _stadium_map(tmp_path / "stadiums.csv")
    output = tmp_path / "raw"

    def fail_hourly(*args, **kwargs):
        raise AssertionError("dry-run must not call Meteostat")

    monkeypatch.setattr("meteostat.hourly", fail_hourly)
    exit_code = cli_main(
        [
            "collect",
            "mlb",
            "--season",
            "2025",
            "--start-date",
            "2025-04-01",
            "--end-date",
            "2025-04-02",
            "--retrosheet-path",
            str(game_log),
            "--fetch-weather",
            "--weather-provider",
            "meteostat",
            "--stadium-map-path",
            str(stadium_map),
            "--output-raw-dir",
            str(output),
            "--dry-run",
        ]
    )

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert "weather_meteostat" in summary["planned_sources"]
    assert summary["writes_performed"] is False
    assert not output.exists()


def test_no_station_records_no_nearby_station_without_hourly_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_log = _legacy_log(tmp_path / "gl2025.txt")
    stadium_map = _stadium_map(tmp_path / "stadiums.csv")
    monkeypatch.setattr(
        "meteostat.stations.nearby", lambda point: pd.DataFrame()
    )

    def fail_hourly(*args, **kwargs):
        raise AssertionError("hourly lookup must not run without a station")

    monkeypatch.setattr("meteostat.hourly", fail_hourly)
    result = collect_sources(_request(tmp_path, game_log, stadium_map))

    assert {row["status"] for row in _diagnostic_rows(result)} == {
        "no_nearby_station"
    }
    metadata = _weather_manifest_source(
        result, WEATHER_DIAGNOSTICS_FILENAME
    ).metadata
    assert metadata["missing_weather_count"] == 2
    assert metadata["missing_weather_rate"] == 1.0
    assert metadata["reason_counts"] == {"no_nearby_station": 2}
    missing_source = _weather_manifest_source(
        result, WEATHER_MISSING_REPORT_FILENAME
    )
    missing_path = result.collection_dir / missing_source.local_file_path
    assert len(list(csv.DictReader(missing_path.open(encoding="utf-8")))) == 2


def test_station_found_but_no_rows_records_no_hourly_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_log = _legacy_log(tmp_path / "gl2025.txt")
    stadium_map = _stadium_map(tmp_path / "stadiums.csv")
    monkeypatch.setattr("meteostat.hourly", lambda *args, **kwargs: pd.DataFrame())

    result = collect_sources(_request(tmp_path, game_log, stadium_map))
    diagnostics = _diagnostic_rows(result)

    assert {row["status"] for row in diagnostics} == {"no_hourly_rows"}
    assert all(row["nearest_station_id"] == "72503" for row in diagnostics)
    assert all(row["hourly_rows_found_count"] == "0" for row in diagnostics)


def test_invalid_coordinates_are_diagnosed_without_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_log = _legacy_log(tmp_path / "gl2025.txt")
    stadium_map = _stadium_map(
        tmp_path / "stadiums.csv", latitude="not-a-latitude"
    )

    def fail_provider(*args, **kwargs):
        raise AssertionError("invalid coordinates must not reach Meteostat")

    monkeypatch.setattr("meteostat.stations.nearby", fail_provider)
    monkeypatch.setattr("meteostat.hourly", fail_provider)
    result = collect_sources(_request(tmp_path, game_log, stadium_map))

    diagnostics = _diagnostic_rows(result)
    assert {row["status"] for row in diagnostics} == {"invalid_coordinates"}
    assert all(row["latitude"] == "not-a-latitude" for row in diagnostics)


def test_bad_timezone_records_timezone_error_without_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_log = _legacy_log(tmp_path / "gl2025.txt")
    stadium_map = _stadium_map(
        tmp_path / "stadiums.csv", timezone_name="Not/A_Timezone"
    )

    def fail_provider(*args, **kwargs):
        raise AssertionError("bad timezone must not reach Meteostat")

    monkeypatch.setattr("meteostat.stations.nearby", fail_provider)
    monkeypatch.setattr("meteostat.hourly", fail_provider)
    result = collect_sources(_request(tmp_path, game_log, stadium_map))

    assert {row["status"] for row in _diagnostic_rows(result)} == {
        "timezone_error"
    }


@pytest.mark.parametrize("roof_type", ("dome", "fixed_roof"))
def test_indoor_roof_types_are_skipped_without_fake_weather(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, roof_type: str
) -> None:
    game_log = _legacy_log(tmp_path / "gl2025.txt")
    stadium_map = _stadium_map(tmp_path / "stadiums.csv", roof_type=roof_type)

    def fail_provider(*args, **kwargs):
        raise AssertionError("indoor games must not reach Meteostat")

    monkeypatch.setattr("meteostat.stations.nearby", fail_provider)
    monkeypatch.setattr("meteostat.hourly", fail_provider)
    result = collect_sources(_request(tmp_path, game_log, stadium_map))

    assert {row["status"] for row in _diagnostic_rows(result)} == {
        "indoor_or_roofed"
    }
    weather = _weather_manifest_source(result, WEATHER_FILENAME)
    assert weather.row_count == 0
    assert weather.metadata["weather_summary"][
        "indoor_or_roofed_skipped_count"
    ] == 2


def test_missing_weather_warnings_are_one_summary_not_one_per_game(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_log = _legacy_log(tmp_path / "gl2025.txt")
    stadium_map = _stadium_map(tmp_path / "stadiums.csv")
    monkeypatch.setattr("meteostat.hourly", lambda *args, **kwargs: pd.DataFrame())

    result = collect_sources(_request(tmp_path, game_log, stadium_map))
    weather_summaries = [
        warning for warning in result.warnings if warning.startswith("Weather summary:")
    ]

    assert len(weather_summaries) == 1
    assert "games processed=2" in weather_summaries[0]
    assert "no_hourly_rows=2" in weather_summaries[0]
    assert "NYA202504010" not in " ".join(result.warnings)


def test_cli_console_includes_structured_weather_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    game_log = _legacy_log(tmp_path / "gl2025.txt")
    stadium_map = _stadium_map(tmp_path / "stadiums.csv")
    monkeypatch.setattr("meteostat.hourly", lambda *args, **kwargs: pd.DataFrame())

    exit_code = cli_main(
        [
            "collect", "mlb", "--season", "2025", "--start-date", "2025-04-01",
            "--end-date", "2025-04-02", "--retrosheet-path", str(game_log),
            "--fetch-weather", "--weather-provider", "meteostat",
            "--stadium-map-path", str(stadium_map), "--output-raw-dir",
            str(tmp_path / "raw"),
        ]
    )

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)["weather_summary"]
    assert summary["games_processed"] == 2
    assert summary["weather_found"] == 0
    assert summary["missing_weather"] == 2
    assert summary["missing_by_reason"] == {"no_hourly_rows": 2}
    assert summary["top_missing_park_ids"] == {"NYC21": 2}
    assert summary["indoor_or_roofed_skipped_count"] == 0


def test_weather_collection_rejects_protected_output_folder_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_log = _legacy_log(tmp_path / "gl2025.txt")
    stadium_map = _stadium_map(tmp_path / "stadiums.csv")
    request = _request(tmp_path, game_log, stadium_map)
    protected_request = CollectionRequest(
        sport=request.sport,
        season=request.season,
        start_date=request.start_date,
        end_date=request.end_date,
        output_raw_dir=tmp_path / "outputs",
        collection_id=request.collection_id,
        collection_timestamp=request.collection_timestamp,
        source_options=request.source_options,
    )

    def fail_provider(*args, **kwargs):
        raise AssertionError("protected output must fail before provider access")

    monkeypatch.setattr("meteostat.stations.nearby", fail_provider)
    monkeypatch.setattr("meteostat.hourly", fail_provider)
    with pytest.raises(ProtectedPathError, match="protected path component"):
        collect_sources(protected_request)
    assert not (tmp_path / "outputs").exists()
