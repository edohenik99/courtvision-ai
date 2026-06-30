from __future__ import annotations

import json
from pathlib import Path
import shutil

from courtvision.sports.mlb.data.historical_input_pack import (
    HISTORICAL_INPUT_PACK_VERSION,
    PACK_SOURCE_FILES,
    preflight_historical_input_pack,
)
from courtvision.sports.mlb.data_manifest import compute_file_sha256
import scripts.mlb_build_hr_local_dataset as build_cli
import scripts.mlb_preflight_hr_historical_pack as preflight_cli


FIXTURE_PACK = Path(__file__).parent / "fixtures" / "mlb" / "real_aligned_pack"


def _copy_pack(tmp_path: Path) -> Path:
    destination = tmp_path / "aligned_real_pack"
    shutil.copytree(FIXTURE_PACK, destination)
    return destination


def _refresh_manifest_source(
    pack: Path,
    source_name: str,
    *,
    date_range_start: str | None = None,
    date_range_end: str | None = None,
) -> None:
    manifest_path = pack / "input_pack_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(
        item for item in payload["sources"] if item["source_name"] == source_name
    )
    source = pack / PACK_SOURCE_FILES[source_name]
    entry["sha256"] = compute_file_sha256(source)
    entry["byte_size"] = source.stat().st_size
    if date_range_start is not None:
        entry["date_range_start"] = date_range_start
    if date_range_end is not None:
        entry["date_range_end"] = date_range_end
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_valid_aligned_real_pack_passes_preflight(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)

    result = preflight_historical_input_pack(pack)

    assert result.is_valid
    assert result.errors == ()
    assert result.date_range_start.isoformat() == "2024-04-10"
    assert result.date_range_end.isoformat() == "2024-04-10"
    assert result.row_counts == {
        "statcast": 4,
        "retrosheet_games": 1,
        "retrosheet_events": 2,
        "weather": 1,
        "ballpark_factors": 1,
        "odds_snapshot": 2,
        "labeled_batter_games": 2,
    }
    manifest = json.loads(
        (pack / "input_pack_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["manifest_version"] == HISTORICAL_INPUT_PACK_VERSION


def test_missing_retrosheet_labels_fail_preflight(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    (pack / "retrosheet_events.csv").unlink()

    result = preflight_historical_input_pack(pack)

    assert not result.is_valid
    assert any(
        "required input pack file is missing: retrosheet_events.csv" in error
        for error in result.errors
    )


def test_mismatched_dates_fail_preflight_even_with_refreshed_hash(
    tmp_path: Path,
) -> None:
    pack = _copy_pack(tmp_path)
    statcast = pack / "statcast.csv"
    statcast.write_text(
        statcast.read_text(encoding="utf-8").replace("2024-04-10", "2024-04-11"),
        encoding="utf-8",
    )
    _refresh_manifest_source(
        pack,
        "statcast",
        date_range_start="2024-04-11",
        date_range_end="2024-04-11",
    )

    result = preflight_historical_input_pack(pack)

    assert not result.is_valid
    assert any("Statcast game coverage must exactly match" in error for error in result.errors)


def test_mismatched_player_identity_fails_preflight(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    statcast = pack / "statcast.csv"
    statcast.write_text(
        statcast.read_text(encoding="utf-8").replace("592450", "592451"),
        encoding="utf-8",
    )
    _refresh_manifest_source(pack, "statcast")

    result = preflight_historical_input_pack(pack)

    assert not result.is_valid
    assert any(
        "Statcast batter-game identities must exactly match Retrosheet labels" in error
        for error in result.errors
    )


def test_missing_matching_venue_weather_fails_preflight(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    weather = pack / "weather.csv"
    weather.write_text(
        weather.read_text(encoding="utf-8").replace(
            "Yankee Stadium", "Fenway Park"
        ),
        encoding="utf-8",
    )
    _refresh_manifest_source(pack, "weather")

    result = preflight_historical_input_pack(pack)

    assert not result.is_valid
    assert any("weather venue does not match" in error for error in result.errors)


def test_missing_game_venue_fails_preflight(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    games = pack / "retrosheet_games.csv"
    games.write_text(
        games.read_text(encoding="utf-8").replace(
            ",Yankee Stadium,", ",,"
        ),
        encoding="utf-8",
    )
    _refresh_manifest_source(pack, "retrosheet_games")

    result = preflight_historical_input_pack(pack)

    assert not result.is_valid
    assert any("is missing venue_name" in error for error in result.errors)


def test_missing_weather_file_fails_preflight(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    (pack / "weather.csv").unlink()

    result = preflight_historical_input_pack(pack)

    assert not result.is_valid
    assert any(
        "required input pack file is missing: weather.csv" in error
        for error in result.errors
    )


def test_fixture_sample_identity_fails_preflight(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    events = pack / "retrosheet_events.csv"
    events.write_text(
        events.read_text(encoding="utf-8").replace("Aaron Judge", "Sample Slugger"),
        encoding="utf-8",
    )
    _refresh_manifest_source(pack, "retrosheet_events")

    result = preflight_historical_input_pack(pack)

    assert not result.is_valid
    assert any("uses a sample/fixture/synthetic identity" in error for error in result.errors)


def test_minimum_real_row_thresholds_are_enforced(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)

    result = preflight_historical_input_pack(
        pack,
        minimum_row_thresholds={"statcast": 5},
    )

    assert not result.is_valid
    assert any(
        "statcast minimum real row threshold not met: required=5, actual=4" in error
        for error in result.errors
    )


def test_preflight_does_not_write_operational_or_pack_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pack = _copy_pack(tmp_path)
    sentinels = []
    for relative in (
        "outputs/sentinel.txt",
        "data/history/sentinel.txt",
        "runtime/sentinel.txt",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
        sentinels.append(path)
    observed = [*pack.rglob("*"), *sentinels]
    files = [path for path in observed if path.is_file()]
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in files
    }
    before_names = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    monkeypatch.chdir(tmp_path)

    result = preflight_historical_input_pack(pack)

    assert result.is_valid
    assert before == {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in files
    }
    assert before_names == {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}


def test_preflight_cli_and_builder_pack_workflow_are_read_only_by_default(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    pack = _copy_pack(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert preflight_cli.main([str(pack)]) == 0
    assert "preflight_status: valid" in capsys.readouterr().out

    assert build_cli.main(["--historical-input-pack", str(pack)]) == 0
    output = capsys.readouterr().out
    assert "historical_input_pack_preflight: valid" in output
    assert "mode: historical_dry_run" in output
    assert "approval_status: not_approved" in output
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "data" / "history").exists()
    assert not (tmp_path / "runtime").exists()
