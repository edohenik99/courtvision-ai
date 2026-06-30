from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from courtvision.sports.mlb.data.historical_input_pack import (
    INPUT_PACK_MANIFEST_FILENAME,
    PACK_SOURCE_FILES,
)
from courtvision.sports.mlb.data.historical_staging import (
    HistoricalStagingBuildError,
    build_historical_input_pack_staging,
)
from courtvision.sports.mlb.data_manifest import verify_source_manifest_file
import scripts.mlb_stage_hr_historical_pack as staging_cli


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mlb" / "staging_sources"


def _copy_sources(tmp_path: Path) -> Path:
    source_dir = tmp_path / "source_files"
    shutil.copytree(FIXTURE_DIR, source_dir)
    return source_dir


def _build(source_dir: Path, output_dir: Path):
    return build_historical_input_pack_staging(
        statcast_csv=source_dir / "statcast.csv",
        retrosheet_labels_csv=source_dir / "retrosheet_game_labels.csv",
        crosswalk_csv=source_dir / "crosswalk.csv",
        weather_csv=source_dir / "weather.csv",
        ballpark_csv=source_dir / "ballpark_factors.csv",
        odds_context_csv=source_dir / "odds_context.csv",
        output_staging_dir=output_dir,
    )


def _cli_args(source_dir: Path, output_dir: Path) -> list[str]:
    return [
        "--statcast-csv",
        str(source_dir / "statcast.csv"),
        "--retrosheet-labels-csv",
        str(source_dir / "retrosheet_game_labels.csv"),
        "--crosswalk-csv",
        str(source_dir / "crosswalk.csv"),
        "--weather-csv",
        str(source_dir / "weather.csv"),
        "--ballpark-csv",
        str(source_dir / "ballpark_factors.csv"),
        "--odds-context-csv",
        str(source_dir / "odds_context.csv"),
        "--output-staging-dir",
        str(output_dir),
    ]


def test_valid_staging_cli_build_succeeds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_dir = _copy_sources(tmp_path)
    output_dir = tmp_path / "candidate_pack"

    assert staging_cli.main(_cli_args(source_dir, output_dir)) == 0

    output = capsys.readouterr().out
    assert "crosswalk_validation: valid" in output
    assert "input_pack_preflight: valid" in output
    assert "approval_status: not_approved" in output
    assert {path.name for path in output_dir.iterdir()} == {
        *PACK_SOURCE_FILES.values(),
        INPUT_PACK_MANIFEST_FILENAME,
    }


def test_bad_crosswalk_fails_before_writing_final_pack(tmp_path: Path) -> None:
    source_dir = _copy_sources(tmp_path)
    crosswalk = source_dir / "crosswalk.csv"
    crosswalk.write_text(
        crosswalk.read_text(encoding="utf-8").replace(
            "Aaron Judge", "Sample Slugger"
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "candidate_pack"

    with pytest.raises(HistoricalStagingBuildError, match="crosswalk validation failed"):
        _build(source_dir, output_dir)

    assert not output_dir.exists()


def test_missing_weather_fails_without_final_pack(tmp_path: Path) -> None:
    source_dir = _copy_sources(tmp_path)
    (source_dir / "weather.csv").unlink()
    output_dir = tmp_path / "candidate_pack"

    with pytest.raises(HistoricalStagingBuildError, match="weather CSV does not exist"):
        _build(source_dir, output_dir)

    assert not output_dir.exists()


def test_mismatched_odds_player_fails_without_final_pack(tmp_path: Path) -> None:
    source_dir = _copy_sources(tmp_path)
    odds = source_dir / "odds_context.csv"
    odds.write_text(
        odds.read_text(encoding="utf-8").replace("592450", "592451"),
        encoding="utf-8",
    )
    output_dir = tmp_path / "candidate_pack"

    with pytest.raises(
        HistoricalStagingBuildError,
        match="odds player does not match crosswalk",
    ):
        _build(source_dir, output_dir)

    assert not output_dir.exists()


@pytest.mark.parametrize(
    "restricted_name",
    ["outputs", "history", "runtime", "manual-data", "cache"],
)
def test_staging_cannot_write_to_operational_folders(
    tmp_path: Path,
    restricted_name: str,
) -> None:
    source_dir = _copy_sources(tmp_path)
    restricted_root = tmp_path / restricted_name
    restricted_root.mkdir()
    output_dir = restricted_root / "candidate_pack"

    with pytest.raises(
        HistoricalStagingBuildError,
        match="staging output cannot be inside",
    ):
        _build(source_dir, output_dir)

    assert not output_dir.exists()


def test_manifest_verifies_and_records_source_evidence_after_build(
    tmp_path: Path,
) -> None:
    source_dir = _copy_sources(tmp_path)
    output_dir = tmp_path / "candidate_pack"

    result = _build(source_dir, output_dir)
    verification = verify_source_manifest_file(
        output_dir / INPUT_PACK_MANIFEST_FILENAME
    )
    manifest = json.loads(
        (output_dir / INPUT_PACK_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )

    assert result.preflight.is_valid
    assert verification.is_valid, verification.errors
    assert manifest["transformation_classification"] == "research_only_candidate"
    assert manifest["approval_status"] == "not_approved"
    assert manifest["eligible_for_betting"] is False
    assert manifest["kelly_eligible"] is False
    assert manifest["crosswalk"]["validation_status"] == "valid"
    assert manifest["crosswalk"]["sha256"]
    assert len(manifest["input_sources"]) == 6
    assert all(entry["path"] for entry in manifest["input_sources"])
    for entry in manifest["sources"]:
        assert entry["sha256"]
        assert entry["byte_size"] > 0
        assert entry["parsed_row_count"] > 0
        assert entry["date_range_start"] == "2024-04-10"
        assert entry["date_range_end"] == "2024-04-10"
        assert entry["provider_label"]
        assert entry["source_classification"] == "real"
        assert entry["source_paths"]
