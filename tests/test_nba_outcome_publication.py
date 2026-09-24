"""Synthetic offline completion evidence; never a live-provider qualification."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading

import pandas as pd
import pytest

from test_run_research_mode import FakeApiNbaClient, _api_nba_body, _complete_stats, _run, _write_manual_schedule
from scripts.run_research_mode import RESEARCH_OK, _write_stat_projection_csv

DATE = "2026-04-12"


def client():
    return FakeApiNbaClient(games_body=_api_nba_body(DATE), stats_by_game={10403: _complete_stats()})


@pytest.mark.parametrize("case", [
    "provider_unavailable", "no_games", "empty_stats", "live", "unknown_finality",
    "final_plus_live", "partial_multi_game", "partial_team", "partial_player",
    "postponed", "cancelled", "missing_periods", "numeric_manual_schedule",
    "stats_provider_unavailable", "schedule_count", "stats_count", "stats_errors",
    "pagination", "missing_stat", "duplicate_player", "wrong_game", "wrong_date",
    "missing_team", "schedule_errors", "missing_minutes", "score_mismatch",
    "wrong_stat_date", "excess_player_minutes",
])
def test_incomplete_attempt_cannot_claim_completed_namespace(tmp_path, case):
    fake = client()
    game = fake.games_body["response"][0]
    manual_dir = tmp_path / "manual"
    if case == "provider_unavailable":
        fake.games_provider_status = "timeout"
    elif case == "no_games":
        fake.games_body = {"errors": [], "results": 0, "response": []}
    elif case == "empty_stats":
        fake.stats_by_game = {}
    elif case in {"live", "postponed", "cancelled"}:
        game["status"] = {"long": case, "short": 2}
    elif case == "unknown_finality":
        del game["status"]
    elif case in {"final_plus_live", "partial_multi_game"}:
        second = deepcopy(game)
        second["id"] = 10404
        fake.games_body["response"].append(second)
        fake.games_body["results"] = 2
        if case == "final_plus_live":
            second["status"] = {"long": "In Play", "short": 2}
            fake.stats_by_game[10404] = _complete_stats(10404)
    elif case == "partial_team":
        fake.stats_by_game[10403] = _complete_stats()[:5]
    elif case == "partial_player":
        fake.stats_by_game[10403] = _complete_stats()[:-1]
    elif case == "missing_periods":
        del game["periods"]
    elif case == "numeric_manual_schedule":
        fake.games_body = {"errors": [], "results": 0, "response": []}
        _write_manual_schedule(manual_dir, DATE, "10403")
    elif case == "stats_provider_unavailable":
        fake.stats_provider_status = "timeout"
    elif case == "schedule_count":
        fake.games_body["results"] = 2
    elif case == "wrong_date":
        game["date"]["start"] = "2026-04-13T00:00:00Z"
    elif case == "missing_team":
        del game["teams"]["visitors"]["id"]
    elif case == "schedule_errors":
        fake.games_body["errors"] = {"partial": "failure"}
    elif case == "score_mismatch":
        game["scores"]["home"]["points"] += 1
    else:
        original = fake._request

        def request(endpoint, params):
            body = original(endpoint, params)
            if endpoint == "players/statistics":
                if case == "stats_count":
                    body["results"] += 1
                elif case == "stats_errors":
                    body["errors"] = {"partial": "failure"}
                elif case == "pagination":
                    body["paging"] = {"current": 1, "total": 2}
                elif case == "missing_stat":
                    del body["response"][0]["assists"]
                elif case == "duplicate_player":
                    body["response"][1]["player"]["id"] = body["response"][0]["player"]["id"]
                elif case == "wrong_game":
                    body["response"][0]["game"]["id"] = 999
                elif case == "missing_minutes":
                    del body["response"][0]["min"]
                elif case == "wrong_stat_date":
                    body["response"][0]["game"]["date"] = "2026-04-13"
                elif case == "excess_player_minutes":
                    body["response"][0]["min"] = "49:00"
                    body["response"][1]["min"] = "47:00"
            return body

        fake._request = request
    result, output = _run(tmp_path, DATE, fake, manual_dir=manual_dir)
    assert result.stat_projection_path is None
    assert not (output / "nba/outcomes" / f"player_actual_stats_{DATE}.csv").exists()
    evidence = json.loads(result.diagnostics_path.read_text())
    assert evidence["artifact_kind"] == "NBA_OUTCOME_COLLECTION_ATTEMPT"
    assert evidence["canonical_outcome_published"] is False
    assert evidence["outcome_qualification"]["canonical_publication_allowed"] is False
    assert evidence["outcome_qualification"]["reasons"]
    assert evidence["artifacts"]["stat_projection_source_csv"] is None
    assert evidence["market_prop_rows_created"] == evidence["elite_rows_created"] == 0
    assert evidence["kelly_called"] is False
    assert evidence["operator_artifacts_written"] == []


def test_incomplete_then_final_complete_and_immutable_republication(tmp_path):
    fake = client()
    fake.stats_by_game[10403] = _complete_stats()[:-1]
    first, _ = _run(tmp_path, DATE, fake)
    first_bytes = first.diagnostics_path.read_bytes()
    fake.stats_by_game[10403] = _complete_stats()
    final, _ = _run(tmp_path, DATE, fake)
    assert final.status == RESEARCH_OK
    assert final.diagnostics["outcome_qualification"]["classification"] == "FINAL_COMPLETE"
    assert final.diagnostics["canonical_outcome_published"] is True
    assert final.diagnostics_path != first.diagnostics_path
    assert first.diagnostics_path.read_bytes() == first_bytes
    completed_bytes = final.stat_projection_path.read_bytes()
    assert final.diagnostics["canonical_outcome_sha256"] == hashlib.sha256(completed_bytes).hexdigest()
    assert len(pd.read_csv(final.stat_projection_path)) == 10
    calls_before = len(fake.player_stats_calls)
    with pytest.raises(FileExistsError, match="overwrite is prohibited"):
        _run(tmp_path, DATE, fake)
    assert len(fake.player_stats_calls) == calls_before
    assert final.stat_projection_path.read_bytes() == completed_bytes


def test_complete_multi_game_date(tmp_path):
    fake = client()
    second = _api_nba_body(DATE, 10404)["response"][0]
    fake.games_body["response"].append(second)
    fake.games_body["results"] = 2
    fake.stats_by_game[10404] = _complete_stats(10404)
    result, _ = _run(tmp_path, DATE, fake)
    assert result.status == RESEARCH_OK
    assert len(pd.read_csv(result.stat_projection_path)) == 20


def test_csv_serialization_failure_does_not_leave_canonical_path(tmp_path, monkeypatch):
    destination = tmp_path / "nba/outcomes/player_actual_stats_2026-04-12.csv"

    def fail_after_partial_write(frame, stream, **kwargs):
        stream.write("partial bytes")
        raise OSError("synthetic interrupted serialization")

    monkeypatch.setattr(pd.DataFrame, "to_csv", fail_after_partial_write)
    with pytest.raises(OSError, match="interrupted"):
        _write_stat_projection_csv(destination, [])
    assert not destination.exists()
    assert not list(destination.parent.glob("*.partial"))


def test_concurrent_completed_publication_has_one_atomic_winner(tmp_path):
    destination = tmp_path / "nba/outcomes/player_actual_stats_2026-04-12.csv"
    barrier = threading.Barrier(2)

    def publish(points):
        barrier.wait()
        try:
            _write_stat_projection_csv(destination, [{"points": points}])
            return points
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, [10, 20]))
    winners = [value for value in results if value is not None]
    assert len(winners) == 1
    assert pd.read_csv(destination)["points"].tolist() == winners
    assert not list(destination.parent.glob("*.partial"))
