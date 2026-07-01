from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tools.validate_live_hr_data import (
    REQUIRED_COLUMNS,
    main,
    validate_live_hr_data,
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "snapshot_time": "2026-07-01T18:20:52Z",
        "event_id": "event-1",
        "commence_time": "2026-07-02T00:11:00Z",
        "home_team": "Toronto Blue Jays",
        "away_team": "New York Mets",
        "bookmaker_key": "fanduel",
        "bookmaker": "FanDuel",
        "bookmaker_last_update": "2026-07-01T18:20:52Z",
        "market": "batter_home_runs_alternate",
        "market_last_update": "2026-07-01T18:20:52Z",
        "player": "Example Batter",
        "side": "Over",
        "price": 320,
        "point": 0.5,
        "hr_label": "1+ HR",
    }
    row.update(overrides)
    return row


def _write_csv(
    tmp_path: Path,
    rows: list[dict[str, object]],
    *,
    columns: tuple[str, ...] = REQUIRED_COLUMNS,
) -> Path:
    path = tmp_path / "live_hr_props_master.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_valid_file_reports_counts_and_cli_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_csv(
        tmp_path,
        [
            _row(),
            _row(
                snapshot_time="2026-07-02T01:00:00Z",
                event_id="event-2",
                player="Second Batter",
                bookmaker_key="draftkings",
                bookmaker="DraftKings",
            ),
        ],
    )

    report = validate_live_hr_data(path)

    assert report.valid
    assert report.duplicate_count == 0
    assert report.bookmaker_counts == {"draftkings": 1, "fanduel": 1}
    assert report.snapshot_date_counts == {"2026-07-01": 1, "2026-07-02": 1}
    assert main([str(path)]) == 0
    output = capsys.readouterr().out
    assert "Live HR data validation: VALID" in output
    assert "Counts by bookmaker:" in output
    assert "Counts by game:" in output
    assert "Counts by snapshot date:" in output


def test_missing_file_and_missing_schema_exit_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.csv"
    assert main([str(missing)]) == 1
    assert "file does not exist" in capsys.readouterr().out

    path = _write_csv(
        tmp_path,
        [_row()],
        columns=tuple(column for column in REQUIRED_COLUMNS if column != "hr_label"),
    )
    report = validate_live_hr_data(path)
    assert not report.valid
    assert "missing required columns: hr_label" in report.errors


@pytest.mark.parametrize(
    ("overrides", "error_text"),
    [
        ({"player": "  "}, "missing player names"),
        ({"side": "Under"}, "side must be 'Over'"),
        ({"point": 1.5}, "point must be 0.5"),
        ({"hr_label": "Home Run"}, "hr_label must be '1+ HR'"),
        ({"event_id": ""}, "identity field 'event_id' is blank"),
        ({"bookmaker_key": ""}, "identity field 'bookmaker_key' is blank"),
        ({"market": ""}, "identity field 'market' is blank"),
        ({"snapshot_time": "not-a-date"}, "snapshot_time must contain a valid date"),
    ],
)
def test_invalid_row_values_exit_one(
    tmp_path: Path, overrides: dict[str, object], error_text: str
) -> None:
    path = _write_csv(tmp_path, [_row(**overrides)])

    report = validate_live_hr_data(path)

    assert not report.valid
    assert any(error_text in error for error in report.errors)
    assert main([str(path)]) == 1


def test_duplicate_identity_on_same_snapshot_date_is_invalid(
    tmp_path: Path,
) -> None:
    path = _write_csv(
        tmp_path,
        [
            _row(snapshot_time="2026-07-01T18:00:00Z", price=300),
            _row(snapshot_time="2026-07-01T20:00:00Z", price=350),
        ],
    )

    report = validate_live_hr_data(path)

    assert not report.valid
    assert report.duplicate_count == 1
    assert any("found 1 duplicate row" in error for error in report.errors)


def test_same_identity_on_different_snapshot_dates_is_valid(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        [
            _row(snapshot_time="2026-07-01T18:00:00Z"),
            _row(snapshot_time="2026-07-02T18:00:00Z"),
        ],
    )

    report = validate_live_hr_data(path)

    assert report.valid
    assert report.duplicate_count == 0
