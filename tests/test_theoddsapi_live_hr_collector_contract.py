from __future__ import annotations

import argparse
import csv
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import Mock
from uuid import uuid4

import dotenv
import pytest
import requests


COLLECTOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "theoddsapi_live_hr_collector.py"
)


def _load_collector(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    api_key: str | None = "contract-test-key",
) -> ModuleType:
    if api_key is None:
        monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    else:
        monkeypatch.setenv("THE_ODDS_API_KEY", api_key)

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_args, **_kwargs: False)

    real_mkdir = Path.mkdir

    def suppress_import_mkdir(
        _path: Path, *_args: object, **_kwargs: object
    ) -> None:
        return None

    monkeypatch.setattr(Path, "mkdir", suppress_import_mkdir)

    module_name = f"theoddsapi_live_hr_collector_contract_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, COLLECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    try:
        spec.loader.exec_module(module)
    finally:
        monkeypatch.setattr(Path, "mkdir", real_mkdir)

    module.DATA_DIR = tmp_path
    module.MASTER_CSV = tmp_path / "live_hr_props_master.csv"
    module.RUN_LOG = tmp_path / "run_log.csv"
    return module


@pytest.fixture
def collector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> ModuleType:
    module = _load_collector(monkeypatch, tmp_path)

    def unexpected_network_call(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("contract tests must not call the network")

    monkeypatch.setattr(requests, "get", unexpected_network_call)
    return module


def _row(snapshot_time: str, *, price: int = 350) -> dict[str, object]:
    return {
        "snapshot_time": snapshot_time,
        "event_id": "event-1",
        "commence_time": "2026-07-02T23:00:00Z",
        "home_team": "Toronto Blue Jays",
        "away_team": "Boston Red Sox",
        "bookmaker_key": "draftkings",
        "bookmaker": "DraftKings",
        "bookmaker_last_update": "2026-07-01T14:00:00Z",
        "market": "batter_home_runs_alternate",
        "market_last_update": "2026-07-01T14:00:00Z",
        "player": "Example Batter",
        "side": "Over",
        "price": price,
        "point": 0.5,
        "hr_label": "1+ HR",
    }


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_import_requires_no_real_key_or_real_data_directory(
    collector: ModuleType, tmp_path: Path
) -> None:
    assert collector.API_KEY == "contract-test-key"
    assert collector.DATA_DIR == tmp_path
    assert collector.MASTER_CSV.parent == tmp_path


def test_missing_key_fails_before_network_or_file_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    network = Mock(side_effect=AssertionError("network call was attempted"))
    monkeypatch.setattr(requests, "get", network)

    with pytest.raises(RuntimeError, match="Missing THE_ODDS_API_KEY"):
        _load_collector(monkeypatch, tmp_path, api_key=None)

    network.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_flatten_keeps_only_named_over_half_home_run_outcomes(
    collector: ModuleType,
) -> None:
    event = {
        "id": "event-1",
        "commence_time": "2026-07-02T23:00:00Z",
        "home_team": "Toronto Blue Jays",
        "away_team": "Boston Red Sox",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "last_update": "2026-07-01T14:00:00Z",
                "markets": [
                    {
                        "key": "batter_home_runs_alternate",
                        "last_update": "2026-07-01T14:00:00Z",
                        "outcomes": [
                            {
                                "description": "Included Batter",
                                "name": "Over",
                                "price": 350,
                                "point": 0.5,
                            },
                            {
                                "description": "Under Batter",
                                "name": "Under",
                                "price": -500,
                                "point": 0.5,
                            },
                            {
                                "description": "Wrong Line Batter",
                                "name": "Over",
                                "price": 900,
                                "point": 1.5,
                            },
                            {
                                "description": "",
                                "name": "Over",
                                "price": 400,
                                "point": 0.5,
                            },
                        ],
                    }
                ],
            }
        ],
    }

    rows = collector.flatten_1_plus_hr(event, "2026-07-01T15:00:00Z")

    assert len(rows) == 1
    assert rows[0]["player"] == "Included Batter"
    assert rows[0]["hr_label"] == "1+ HR"
    assert list(rows[0]) == collector.ROW_FIELDS


def test_safe_request_is_mocked_and_redacts_key(
    collector: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = Mock(
        status_code=200,
        headers={
            "x-requests-used": "1",
            "x-requests-remaining": "499",
            "x-requests-last": "1",
        },
    )
    response.url = (
        "https://api.the-odds-api.com/v4/sports/baseball_mlb/events"
        "?apiKey=contract-test-key"
    )
    response.json.return_value = [{"id": "event-1"}]
    mocked_get = Mock(return_value=response)
    monkeypatch.setattr(requests, "get", mocked_get)

    payload, headers = collector.safe_request(
        "/sports/baseball_mlb/events", {"dateFormat": "iso"}
    )

    assert payload == [{"id": "event-1"}]
    assert headers["x-requests-remaining"] == "499"
    request_params = mocked_get.call_args.kwargs["params"]
    assert request_params["apiKey"] == "contract-test-key"
    output = capsys.readouterr().out
    assert "contract-test-key" not in output
    assert "API_KEY_HIDDEN" in output


def test_successful_run_log_activates_daily_guard_only_for_matching_date(
    collector: ModuleType,
) -> None:
    collector.write_run_log(
        {
            "run_date": "2026-07-01",
            "snapshot_time": "2026-07-01T15:00:00Z",
            "status": "success",
            "events_found": 10,
            "events_scanned": 10,
            "rows_saved": 100,
            "credits_used_this_run": 11,
            "credits_remaining": 489,
            "snapshot_csv": "temporary-snapshot.csv",
            "master_csv": "temporary-master.csv",
            "error": "",
        }
    )

    assert collector.already_ran_today("2026-07-01") is True
    assert collector.already_ran_today("2026-07-02") is False


def test_daily_guard_stops_main_before_any_network_call(
    collector: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    collector.RUN_LOG.write_text(
        "run_date,status\n2026-07-01,success\n", encoding="utf-8"
    )
    fixed_now = collector.parse_iso_z("2026-07-01T15:00:00Z")
    monkeypatch.setattr(collector, "now_utc", lambda: fixed_now)
    monkeypatch.setattr(sys, "argv", [str(COLLECTOR_PATH)])

    collector.main()

    assert "Stopping to protect credits" in capsys.readouterr().out
    assert not collector.MASTER_CSV.exists()


def test_dedupe_only_is_idempotent_and_never_calls_network(
    collector: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector.write_csv(
        collector.MASTER_CSV,
        [
            _row("2026-07-01T14:00:00Z", price=350),
            _row("2026-07-01T15:00:00Z", price=375),
            _row("2026-07-02T14:00:00Z", price=400),
        ],
    )
    monkeypatch.setattr(
        sys, "argv", [str(COLLECTOR_PATH), "--dedupe-only"]
    )

    collector.main()
    first_pass = _read_rows(collector.MASTER_CSV)
    collector.main()
    second_pass = _read_rows(collector.MASTER_CSV)

    assert len(first_pass) == 2
    assert first_pass[0]["price"] == "375"
    assert first_pass[1]["price"] == "400"
    assert second_pass == first_pass


def test_csv_writes_remain_inside_temporary_directory(
    collector: ModuleType, tmp_path: Path
) -> None:
    snapshot = tmp_path / "live_hr_props_test.csv"
    rows = [_row("2026-07-01T15:00:00Z")]

    collector.write_csv(snapshot, rows)
    collector.append_master_csv(collector.MASTER_CSV, rows)

    assert _read_rows(snapshot)[0]["player"] == "Example Batter"
    assert _read_rows(collector.MASTER_CSV)[0]["player"] == "Example Batter"
    assert {path.parent for path in tmp_path.iterdir()} == {tmp_path}



def _event(
    event_id: str,
    commence_time: str,
) -> dict[str, str]:
    return {
        "id": event_id,
        "commence_time": commence_time,
        "home_team": f"{event_id} Home",
        "away_team": f"{event_id} Away",
    }


def test_operating_date_parser_is_strict(
    collector: ModuleType,
) -> None:
    assert collector.parse_operating_date("2026-08-20").isoformat() == "2026-08-20"

    with pytest.raises(
        argparse.ArgumentTypeError,
        match="YYYY-MM-DD",
    ):
        collector.parse_operating_date("20260820")


def test_operating_date_filter_uses_toronto_local_calendar_date(
    collector: ModuleType,
) -> None:
    snapshot = collector.parse_iso_z("2026-08-18T12:00:00Z")

    events = [
        _event("late-aug19", "2026-08-20T03:30:00Z"),
        _event("early-aug20", "2026-08-20T04:40:00Z"),
    ]

    selected = collector.select_operating_date_events(
        events,
        operating_date="2026-08-19",
        snapshot_dt=snapshot,
        max_events=20,
    )

    assert [event["id"] for event in selected] == ["late-aug19"]


def test_full_15_game_operating_date_slate_is_not_truncated(
    collector: ModuleType,
) -> None:
    snapshot = collector.parse_iso_z("2026-08-20T12:00:00Z")

    events = [
        _event(
            f"event-{index:02d}",
            f"2026-08-20T{14 + (index // 2):02d}:{(index % 2) * 30:02d}:00Z",
        )
        for index in range(15)
    ]

    selected = collector.select_operating_date_events(
        list(reversed(events)),
        operating_date="2026-08-20",
        snapshot_dt=snapshot,
        max_events=20,
    )

    assert len(selected) == 15
    assert [event["id"] for event in selected] == [
        event["id"] for event in events
    ]


def test_slate_over_safety_ceiling_fails_closed(
    collector: ModuleType,
) -> None:
    snapshot = collector.parse_iso_z("2026-08-20T12:00:00Z")

    events = [
        _event(
            f"event-{index:02d}",
            f"2026-08-20T{14 + (index // 2):02d}:{(index % 2) * 30:02d}:00Z",
        )
        for index in range(15)
    ]

    with pytest.raises(
        RuntimeError,
        match=r"15 eligible events.*--max-events=12.*Refusing partial-slate",
    ):
        collector.select_operating_date_events(
            events,
            operating_date="2026-08-20",
            snapshot_dt=snapshot,
            max_events=12,
        )


def test_games_inside_30_minute_safety_zone_are_excluded(
    collector: ModuleType,
) -> None:
    snapshot = collector.parse_iso_z("2026-08-20T17:00:00Z")

    events = [
        _event("too-close", "2026-08-20T17:29:59Z"),
        _event("boundary", "2026-08-20T17:30:00Z"),
        _event("later", "2026-08-20T18:00:00Z"),
    ]

    selected = collector.select_operating_date_events(
        events,
        operating_date="2026-08-20",
        snapshot_dt=snapshot,
        max_events=20,
    )

    assert [event["id"] for event in selected] == [
        "boundary",
        "later",
    ]


def test_operating_date_selection_is_deterministic(
    collector: ModuleType,
) -> None:
    snapshot = collector.parse_iso_z("2026-08-20T12:00:00Z")

    events = [
        _event("event-c", "2026-08-20T20:00:00Z"),
        _event("event-b", "2026-08-20T18:00:00Z"),
        _event("event-a", "2026-08-20T18:00:00Z"),
    ]

    selected = collector.select_operating_date_events(
        events,
        operating_date="2026-08-20",
        snapshot_dt=snapshot,
        max_events=20,
    )

    assert [event["id"] for event in selected] == [
        "event-a",
        "event-b",
        "event-c",
    ]


def test_main_refuses_oversized_slate_before_event_odds_requests(
    collector: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = collector.parse_iso_z("2026-08-20T12:00:00Z")

    events = [
        _event(
            f"event-{index:02d}",
            f"2026-08-20T{14 + (index // 2):02d}:{(index % 2) * 30:02d}:00Z",
        )
        for index in range(15)
    ]

    get_events = Mock(
        return_value=(
            events,
            {
                "x-requests-last": "0",
                "x-requests-remaining": "100",
            },
        )
    )

    event_odds = Mock(
        side_effect=AssertionError(
            "event-level provider request must not occur"
        )
    )

    monkeypatch.setattr(collector, "now_utc", lambda: fixed_now)
    monkeypatch.setattr(collector, "get_events", get_events)
    monkeypatch.setattr(collector, "get_event_hr_odds", event_odds)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(COLLECTOR_PATH),
            "--force",
            "--quiet",
            "--operating-date",
            "2026-08-20",
            "--max-events",
            "12",
        ],
    )

    with pytest.raises(
        RuntimeError,
        match=r"15 eligible events.*Refusing partial-slate",
    ):
        collector.main()

    get_events.assert_called_once()
    event_odds.assert_not_called()

    assert not list(
        collector.DATA_DIR.glob("live_hr_props_20260820_*.csv")
    )



def test_default_operating_date_drives_daily_guard_across_utc_midnight(
    collector: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 03:30 UTC on Aug20 is still 23:30 on Aug19 in Toronto.
    fixed_now = collector.parse_iso_z("2026-08-20T03:30:00Z")

    collector.RUN_LOG.write_text(
        "run_date,status\n"
        "2026-08-19,success\n",
        encoding="utf-8",
    )

    get_events = Mock(
        side_effect=AssertionError(
            "daily guard should stop before provider event discovery"
        )
    )

    monkeypatch.setattr(
        collector,
        "now_utc",
        lambda: fixed_now,
    )

    monkeypatch.setattr(
        collector,
        "get_events",
        get_events,
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [str(COLLECTOR_PATH)],
    )

    collector.main()

    get_events.assert_not_called()

    output = capsys.readouterr().out

    assert "Collector already ran today: 2026-08-19" in output
    assert "Stopping to protect credits" in output


def test_explicit_operating_date_drives_daily_guard(
    collector: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixed_now = collector.parse_iso_z("2026-08-20T16:00:00Z")

    collector.RUN_LOG.write_text(
        "run_date,status\n"
        "2026-08-19,success\n",
        encoding="utf-8",
    )

    get_events = Mock(
        side_effect=AssertionError(
            "explicit-date daily guard should stop before provider access"
        )
    )

    monkeypatch.setattr(
        collector,
        "now_utc",
        lambda: fixed_now,
    )

    monkeypatch.setattr(
        collector,
        "get_events",
        get_events,
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(COLLECTOR_PATH),
            "--operating-date",
            "2026-08-19",
        ],
    )

    collector.main()

    get_events.assert_not_called()

    output = capsys.readouterr().out

    assert "Collector already ran today: 2026-08-19" in output
