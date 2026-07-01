from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tools.run_live_hr_daily_check import main
from tools.validate_live_hr_data import REQUIRED_COLUMNS


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


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_default_check_is_read_only_and_reports_health(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    path = tmp_path / "live_hr_props_master.csv"
    _write_csv(
        path,
        [
            _row(),
            _row(
                event_id="event-2",
                player="Second Batter",
                bookmaker_key="draftkings",
                bookmaker="DraftKings",
            ),
        ],
    )
    before = path.read_bytes()

    assert main([str(path)]) == 0

    assert path.read_bytes() == before
    output = capsys.readouterr().out
    assert "Live HR daily check: VALID" in output
    assert "Rows: 2" in output
    assert "Duplicates: 0" in output
    assert "Snapshot dates: 2026-07-01=2" in output
    assert "Bookmakers: draftkings=1, fanduel=1" in output
    assert "Games: 2" in output


def test_duplicate_data_is_invalid_and_unchanged_without_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "live_hr_props_master.csv"
    _write_csv(
        path,
        [
            _row(snapshot_time="2026-07-01T18:00:00Z", price=300),
            _row(snapshot_time="2026-07-01T20:00:00Z", price=350),
        ],
    )
    before = path.read_bytes()

    assert main([str(path)]) == 1

    assert path.read_bytes() == before
    output = capsys.readouterr().out
    assert "Live HR daily check: INVALID" in output
    assert "Duplicates: 1" in output


def test_dedupe_keeps_latest_snapshot_then_validates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "live_hr_props_master.csv"
    _write_csv(
        path,
        [
            _row(snapshot_time="2026-07-01T20:00:00Z", price=350),
            _row(snapshot_time="2026-07-01T18:00:00Z", price=300),
            _row(
                snapshot_time="2026-07-02T18:00:00Z",
                price=400,
            ),
        ],
    )

    assert main(["--dedupe", str(path)]) == 0

    rows = _read_rows(path)
    assert len(rows) == 2
    assert [(row["snapshot_time"], row["price"]) for row in rows] == [
        ("2026-07-01T20:00:00Z", "350"),
        ("2026-07-02T18:00:00Z", "400"),
    ]
    output = capsys.readouterr().out
    assert "Dedupe rows: 3 -> 2" in output
    assert "Live HR daily check: VALID" in output
    assert "Rows: 2" in output
    assert "Duplicates: 0" in output
