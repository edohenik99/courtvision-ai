from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import urllib.request

import pytest

from courtvision.sports.mlb.data.statcast_ingestion import (
    STATCAST_SOURCE_NAME,
    StatcastIngestionError,
    build_statcast_query_url,
    download_statcast_csv,
    ingest_local_statcast_csv,
    statcast_row_to_dict,
    statcast_row_to_json,
)
from courtvision.sports.mlb.data_manifest import validate_source_manifest
from scripts.mlb_ingest_statcast import main


FIXTURE = Path(__file__).parent / "fixtures" / "mlb" / "statcast_sample.csv"
COLLECTED_AT = datetime(2026, 6, 19, 17, 0, tzinfo=timezone.utc)


def _ingest_fixture(**kwargs: object):
    return ingest_local_statcast_csv(
        FIXTURE,
        collected_at=COLLECTED_AT,
        **kwargs,
    )


def test_fixture_parses_labels_missing_numbers_and_deterministic_rows() -> None:
    result = _ingest_fixture()
    home_run, non_home_run = result.rows

    assert len(result.rows) == 2
    assert home_run.is_home_run is True
    assert home_run.event_type == "home_run"
    assert home_run.launch_speed == 109.8
    assert home_run.is_barrel is True
    assert non_home_run.is_home_run is False
    assert non_home_run.event_type == "double"
    assert non_home_run.estimated_ba is None
    assert non_home_run.estimated_woba is None
    assert non_home_run.is_barrel is None
    assert statcast_row_to_json(home_run) == statcast_row_to_json(home_run)
    assert json.loads(statcast_row_to_json(home_run)) == statcast_row_to_dict(home_run)


def test_normalized_row_has_research_fields_and_no_decision_fields() -> None:
    payload = statcast_row_to_dict(_ingest_fixture().rows[0])
    forbidden = {
        "ev",
        "fair_probability",
        "unit_size",
        "staking",
        "kelly",
        "bankroll",
        "elite",
        "strong",
        "recommendation",
    }

    assert payload["sport"] == "MLB"
    assert payload["league"] == "MLB"
    assert payload["source"] == STATCAST_SOURCE_NAME
    assert payload["source_type"] == "historical"
    assert forbidden.isdisjoint(payload)


def test_manifest_uses_phase3b_validation_checksum_and_date_range() -> None:
    result = _ingest_fixture()
    manifest = result.manifest

    assert validate_source_manifest(manifest).is_valid
    assert manifest.source_name == STATCAST_SOURCE_NAME
    assert manifest.source_type == "historical"
    assert manifest.data_domain == "statcast"
    assert manifest.date_range_start == date(2025, 4, 1)
    assert manifest.date_range_end == date(2025, 4, 2)
    assert manifest.as_of_date == date(2025, 4, 2)
    assert manifest.raw_path == FIXTURE.resolve()
    assert manifest.normalized_path is None
    assert manifest.row_count == 2
    assert manifest.checksum == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()


def test_required_columns_are_enforced(tmp_path: Path) -> None:
    path = tmp_path / "missing.csv"
    path.write_text("game_date,game_pk\n2025-04-01,1\n", encoding="utf-8")

    with pytest.raises(StatcastIngestionError, match="missing required columns"):
        ingest_local_statcast_csv(path, collected_at=COLLECTED_AT)


def test_invalid_date_fails_with_row_context(tmp_path: Path) -> None:
    path = tmp_path / "invalid-date.csv"
    path.write_text(
        FIXTURE.read_text(encoding="utf-8").replace("2025-04-01", "04/01/2025", 1),
        encoding="utf-8",
    )

    with pytest.raises(StatcastIngestionError, match=r"row 2: game_date"):
        ingest_local_statcast_csv(path, collected_at=COLLECTED_AT)


def test_as_of_date_cannot_predate_input_events() -> None:
    with pytest.raises(StatcastIngestionError, match="latest game_date"):
        _ingest_fixture(as_of_date=date(2025, 4, 1))


def test_dry_run_api_and_cli_create_no_output_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "dry-run-output"

    result = _ingest_fixture(output_dir=output_dir)
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

    result = _ingest_fixture(
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
    assert all(output_dir.resolve() in path.parents for path in paths if path is not None)
    assert result.raw_output_path.read_bytes() == FIXTURE.read_bytes()
    assert len(result.normalized_output_path.read_text(encoding="utf-8").splitlines()) == 2
    manifest_payload = json.loads(result.manifest_output_path.read_text(encoding="utf-8"))
    assert manifest_payload["normalized_path"] == str(result.normalized_output_path)


def test_network_is_blocked_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def fail_if_called(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("network should not be called")

    monkeypatch.setattr(urllib.request, "urlopen", fail_if_called)

    with pytest.raises(PermissionError, match="allow_network=True"):
        download_statcast_csv("2025-04-01", "2025-04-02", tmp_path / "raw.csv")

    assert called is False
    assert not (tmp_path / "raw.csv").exists()


def test_explicit_network_path_is_mocked_and_writes_only_requested_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"game_date,game_pk\n2025-04-01,1\n"

    def fake_urlopen(request: object, timeout: float) -> io.BytesIO:
        assert "statcast_search/csv" in request.full_url
        assert timeout == 60.0
        return io.BytesIO(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    destination = tmp_path / "mocked.csv"

    written = download_statcast_csv(
        "2025-04-01",
        "2025-04-02",
        destination,
        allow_network=True,
    )

    assert written == destination.resolve()
    assert destination.read_bytes() == payload
    assert list(tmp_path.iterdir()) == [destination]


def test_query_builder_validates_dates_and_guards_large_ranges() -> None:
    url = build_statcast_query_url("2025-04-01", "2025-04-02")
    params = parse_qs(urlparse(url).query)

    assert params["game_date_gt"] == ["2025-04-01"]
    assert params["game_date_lt"] == ["2025-04-02"]
    with pytest.raises(StatcastIngestionError, match="start_date must not be after"):
        build_statcast_query_url("2025-04-02", "2025-04-01")
    with pytest.raises(StatcastIngestionError, match="confirm_large_range=True"):
        build_statcast_query_url("2025-04-01", "2025-06-01")
    assert "game_date_gt=2025-04-01" in build_statcast_query_url(
        "2025-04-01",
        "2025-06-01",
        confirm_large_range=True,
    )
