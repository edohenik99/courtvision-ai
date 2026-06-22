from __future__ import annotations

from dataclasses import replace
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Callable
import urllib.request

import pytest

from courtvision.sports.mlb.data import ballpark_factors
from courtvision.sports.mlb.data.ballpark_factors import (
    BallparkFactorError,
    ballpark_row_to_context,
    ballpark_row_to_dict,
    ballpark_row_to_json,
    find_ballpark_by_venue,
    ingest_local_ballpark_factors_csv,
    load_ballpark_factor_rows,
    normalize_venue_name,
    validate_ballpark_factor_row,
)
from courtvision.sports.mlb.data_manifest import validate_source_manifest
from courtvision.sports.mlb.research_context import MLBBallparkContext
from scripts.mlb_ingest_ballpark_factors import main


FIXTURE = (
    Path(__file__).parent / "fixtures" / "mlb" / "ballpark_factors_sample.csv"
)
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
}


def _fixture_copy(tmp_path: Path, transform: Callable[[str], str]) -> Path:
    text = FIXTURE.read_text(encoding="utf-8")
    path = tmp_path / "ballpark.csv"
    path.write_text(transform(text), encoding="utf-8")
    return path


def test_ballpark_fixture_parses_as_static_research_rows() -> None:
    rows = load_ballpark_factor_rows(FIXTURE)

    assert len(rows) == 3
    assert rows[0].sport == "MLB"
    assert rows[0].league == "MLB"
    assert rows[0].source == "ballpark_factors_static_fixture"
    assert rows[0].source_type == "static"
    assert "research only" in rows[0].warnings[0]


def test_required_columns_are_enforced(tmp_path: Path) -> None:
    def remove_header(text: str) -> str:
        lines = text.splitlines()
        headers = lines[0].split(",")
        index = headers.index("venue_name")
        rewritten = []
        for line in lines:
            values = line.split(",")
            rewritten.append(",".join(values[:index] + values[index + 1 :]))
        return "\n".join(rewritten) + "\n"

    path = _fixture_copy(tmp_path, remove_header)

    with pytest.raises(BallparkFactorError, match="missing required columns"):
        load_ballpark_factor_rows(path)


def test_missing_venue_name_fails_with_row_context(tmp_path: Path) -> None:
    path = _fixture_copy(
        tmp_path, lambda text: text.replace("Rogers Centre,TOR", ",TOR", 1)
    )

    with pytest.raises(BallparkFactorError, match=r"row 2: venue_name"):
        load_ballpark_factor_rows(path)


def test_missing_park_factor_fails_complete_row_validation(tmp_path: Path) -> None:
    path = _fixture_copy(
        tmp_path, lambda text: text.replace("Rogers Centre,TOR,1.08", "Rogers Centre,TOR,", 1)
    )

    with pytest.raises(BallparkFactorError, match="park_factor_hr is required"):
        load_ballpark_factor_rows(path)

    row = load_ballpark_factor_rows(FIXTURE)[0]
    incomplete = replace(row, park_factor_hr=None, data_quality="incomplete_static")
    with pytest.raises(BallparkFactorError, match="park_factor_hr is required"):
        validate_ballpark_factor_row(incomplete)
    assert ballpark_row_to_context(incomplete).park_factor_hr is None


def test_invalid_numeric_values_fail_with_row_context(tmp_path: Path) -> None:
    path = _fixture_copy(
        tmp_path,
        lambda text: text.replace(",1.08,1.10,", ",not-a-number,1.10,", 1),
    )

    with pytest.raises(BallparkFactorError, match=r"row 2: park_factor_hr"):
        load_ballpark_factor_rows(path)


def test_optional_numeric_empty_values_become_none() -> None:
    row = load_ballpark_factor_rows(FIXTURE)[2]

    assert row.handedness_factor_lhb is None
    assert row.handedness_factor_rhb is None
    assert row.roof_type is None
    assert row.data_quality == "partial_static"


@pytest.mark.parametrize(
    "name",
    [" Example Open Park ", "EXAMPLE OPEN PARK", "Example--Open_Park"],
)
def test_venue_normalization_is_deterministic(name: str) -> None:
    assert normalize_venue_name(name) == "example open park"


def test_unknown_venue_returns_none_without_fabricating_context() -> None:
    rows = load_ballpark_factor_rows(FIXTURE)

    assert find_ballpark_by_venue(rows, "Unknown Park") is None


def test_duplicate_normalized_venues_fail_clearly(tmp_path: Path) -> None:
    duplicate = (
        "rogers--centre,TOR2,1.01,1.01,1.01,10,330,400,330,open,"
        "ballpark_factors_static_fixture,static,fixture-v1,"
        "2026-06-19T17:00:00Z,2026-06-19\n"
    )
    path = _fixture_copy(tmp_path, lambda text: text + duplicate)

    with pytest.raises(BallparkFactorError, match="duplicate or ambiguous venue"):
        load_ballpark_factor_rows(path)


def test_normalized_serialization_is_deterministic_and_decision_free() -> None:
    row = load_ballpark_factor_rows(FIXTURE)[0]

    assert ballpark_row_to_json(row) == ballpark_row_to_json(row)
    assert json.loads(ballpark_row_to_json(row)) == ballpark_row_to_dict(row)
    assert FORBIDDEN_FIELDS.isdisjoint(ballpark_row_to_dict(row))


def test_ballpark_row_maps_without_guessing_into_existing_context() -> None:
    row = load_ballpark_factor_rows(FIXTURE)[0]

    context = ballpark_row_to_context(row)

    assert isinstance(context, MLBBallparkContext)
    assert context.park_factor_hr == 1.08
    assert context.handedness_factor == {"LHB": 1.1, "RHB": 1.06}
    assert context.dimensions == {"LF": 330.0, "CF": 400.0, "RF": 325.0}
    assert context.roof_type == "open"
    assert context.source_type == "static"
    assert context.mode == "research"


def test_missing_optional_values_stay_missing_in_context() -> None:
    row = load_ballpark_factor_rows(FIXTURE)[2]
    context = ballpark_row_to_context(row)

    assert context.handedness_factor is None
    assert context.roof_type is None
    assert context.altitude == 15.0


def test_manifest_uses_phase3b_ballpark_contract_and_checksum() -> None:
    manifest = ingest_local_ballpark_factors_csv(FIXTURE).manifest

    assert validate_source_manifest(manifest).is_valid
    assert manifest.source_name == "ballpark_factors_static_fixture"
    assert manifest.source_type == "static"
    assert manifest.data_domain == "ballpark"
    assert manifest.source_version == "fixture-v1"
    assert manifest.as_of_date == date(2026, 6, 19)
    assert manifest.row_count == 3
    assert manifest.file_count == 1
    assert manifest.checksum == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert manifest.files[0].checksum == manifest.checksum


def test_manifest_calls_phase3b_checksum_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    real_helper = ballpark_factors.compute_file_sha256

    def recording_helper(path: str | Path) -> str:
        calls.append(Path(path))
        return real_helper(path)

    monkeypatch.setattr(ballpark_factors, "compute_file_sha256", recording_helper)

    ingest_local_ballpark_factors_csv(FIXTURE)

    assert calls == [FIXTURE.resolve()]


def test_dry_run_api_and_cli_create_no_output_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "dry-run-output"

    result = ingest_local_ballpark_factors_csv(FIXTURE, output_dir=output_dir)
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

    result = ingest_local_ballpark_factors_csv(
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

    ingest_local_ballpark_factors_csv(FIXTURE)

    assert called is False
