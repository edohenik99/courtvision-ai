from __future__ import annotations

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
