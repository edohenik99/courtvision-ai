from __future__ import annotations

from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.mlb_validate_stadium_map import (
    StadiumMapValidationError,
    main,
    validate_stadium_map,
)


FIXTURES = Path(__file__).parent / "fixtures" / "mlb"
VALID_MAP = FIXTURES / "stadium_map_valid.csv"
GAME_LOG = FIXTURES / "retrosheet_weather_game_log.csv"
HEADER = "park_id,stadium_name,latitude,longitude,timezone,elevation_m\n"


def _write_map(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "stadiums.csv"
    path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_valid_map_covers_all_game_log_parks_and_cli_succeeds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = validate_stadium_map(VALID_MAP, GAME_LOG)

    assert report.stadium_count == 2
    assert report.game_count == 2
    assert report.covered_park_ids == ("BOS07", "NYC21")
    assert main(["--stadium-map", str(VALID_MAP), "--game-log", str(GAME_LOG)]) == 0
    assert "2 covered Retrosheet park ID(s)" in capsys.readouterr().out


def test_required_columns_are_enforced(tmp_path: Path) -> None:
    path = tmp_path / "missing-timezone.csv"
    path.write_text(
        "park_id,stadium_name,latitude,longitude,elevation_m\n"
        "NYC21,Yankee Stadium,40.8296,-73.9262,17\n",
        encoding="utf-8",
    )

    with pytest.raises(StadiumMapValidationError, match="missing required columns: timezone"):
        validate_stadium_map(path, GAME_LOG)


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            (
                "NYC21,Yankee Stadium,40.8296,-73.9262,America/New_York,17",
                "nyc21,Duplicate,40.8,-73.9,America/New_York,17",
            ),
            "duplicate park_id 'NYC21'",
        ),
        ((" ,Blank Park,40.8,-73.9,America/New_York,17",), "park_id must not be blank"),
    ],
)
def test_duplicate_and_blank_park_ids_are_rejected(
    tmp_path: Path, rows: tuple[str, ...], message: str
) -> None:
    with pytest.raises(StadiumMapValidationError, match=message):
        validate_stadium_map(_write_map(tmp_path, *rows), GAME_LOG)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (
            "NYC21,Yankee Stadium,91,-73.9262,America/New_York,17",
            "latitude must be between -90 and 90",
        ),
        (
            "NYC21,Yankee Stadium,40.8296,nan,America/New_York,17",
            "longitude must be finite",
        ),
        (
            "NYC21,Yankee Stadium,40.8296,-73.9262,Eastern Standard Time,17",
            "is not a valid IANA timezone",
        ),
    ],
)
def test_coordinates_and_timezone_are_validated(
    tmp_path: Path, row: str, message: str
) -> None:
    with pytest.raises(StadiumMapValidationError, match=message):
        validate_stadium_map(_write_map(tmp_path, row), GAME_LOG)


def test_every_game_log_park_must_be_covered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_map(
        tmp_path,
        "NYC21,Yankee Stadium,40.8296,-73.9262,America/New_York,17",
    )

    assert main(["--stadium-map", str(path), "--game-log", str(GAME_LOG)]) == 2
    assert "missing Retrosheet park IDs: BOS07" in capsys.readouterr().err
