"""Offline regression fixtures for the new-control recovery input boundary."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket

import pytest

from courtvision.sports.mlb.training import hr_prospective_trial as trial
from courtvision.sports.mlb.training import hr_research_baseline as baseline
from tools import mlb_hr_recovery_contract as recovery
from tests.test_mlb_hr_prospective_trial import (
    NOW,
    _activate,
    _git,
    _odds_row,
    _run,
    _snapshot_tree,
    _write_csv,
    workspace,
)


@pytest.mark.parametrize("market", ["batter_home_runs", "batter_home_runs_alternate"])
def test_only_supported_hr_aliases_are_accepted(market: str) -> None:
    baseline._validate_odds_row(_odds_row(market=market))


@pytest.mark.parametrize("market", ["batter_hits", "batter_total_bases", "", "home_runs"])
def test_other_markets_cannot_masquerade_as_over_half_hr(market: str) -> None:
    with pytest.raises(baseline.MLBHRResearchBaselineError, match="supported HR"):
        baseline._validate_odds_row(_odds_row(market=market))


@pytest.mark.parametrize("overrides", [{"side": "Under"}, {"point": 1.5}])
def test_hr_side_and_threshold_remain_strict(overrides: dict[str, object]) -> None:
    with pytest.raises(baseline.MLBHRResearchBaselineError, match="Over"):
        baseline._validate_odds_row(_odds_row(**overrides))


def test_familiar_team_names_do_not_establish_regular_season() -> None:
    row = _odds_row(event_type="")
    status, reason, rule = baseline._event_eligibility_from_row(row)
    assert status == baseline.EVENT_TYPE_UNKNOWN
    assert reason == baseline.EVENT_TYPE_UNKNOWN_EXCLUSION_REASON
    assert rule == "authoritative_event_type_required"


@pytest.mark.parametrize("event_type", ["R", "regular", "regular_season", "regular season"])
def test_explicit_regular_game_type_is_supported(event_type: str) -> None:
    row = _odds_row(event_type="", game_type=event_type)
    status, _, _ = baseline._event_eligibility_from_row(row)
    assert status == baseline.EVENT_REGULAR_SEASON_ELIGIBLE


@pytest.mark.parametrize("event_type", ["S", "P", "W", "postseason", "spring_training", "all_star"])
def test_non_regular_games_are_never_regular_by_club_name(event_type: str) -> None:
    status, _, _ = baseline._event_eligibility_from_row(_odds_row(event_type=event_type))
    assert status != baseline.EVENT_REGULAR_SEASON_ELIGIBLE


def test_conflicting_type_fields_are_quarantined() -> None:
    status, _, rule = baseline._event_eligibility_from_row(
        _odds_row(game_type="P")
    )
    assert status == baseline.EVENT_MANUAL_REVIEW_REQUIRED
    assert rule == "conflicting_authoritative_event_types"


@pytest.mark.parametrize("target", ["2026-08-05", "2026-08-07"])
def test_off_date_cannot_publish_prospective_evidence(
    workspace: dict[str, Path], target: str,
) -> None:
    control = _activate(workspace)
    before = _snapshot_tree(workspace["trial"])
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="current America/Toronto"):
        trial.run_prospective_paper_day(
            target_date=target,
            control_dir=control.control_dir,
            odds_csv=workspace["odds"],
            trial_root=workspace["trial"],
            repository_root=workspace["repository"],
            clock=lambda: NOW,
        )
    assert _snapshot_tree(workspace["trial"]) == before


def test_toronto_date_is_used_across_utc_midnight(workspace: dict[str, Path]) -> None:
    control = _activate(workspace)
    _write_csv(
        workspace["odds"], baseline.ODDS_REQUIRED_COLUMNS,
        [_odds_row(snapshot_time="2026-08-07T00:00:00Z", commence_time="2026-08-07T02:00:00Z")],
    )
    result = trial.run_prospective_paper_day(
        target_date="2026-08-06",
        control_dir=control.control_dir,
        odds_csv=workspace["odds"],
        trial_root=workspace["trial"],
        repository_root=workspace["repository"],
        clock=lambda: datetime(2026, 8, 7, 1, 0, tzinfo=timezone.utc),
        dry_run=True,
    )
    assert result.prediction_count == 1
    assert result.predictions[0]["operating_timezone"] == "America/Toronto"


def test_prediction_cannot_precede_control_creation(workspace: dict[str, Path]) -> None:
    control = _activate(workspace)
    before = _snapshot_tree(workspace["trial"])
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="precedes immutable control"):
        trial.run_prospective_paper_day(
            target_date="2026-08-06",
            control_dir=control.control_dir,
            odds_csv=workspace["odds"],
            trial_root=workspace["trial"],
            repository_root=workspace["repository"],
            clock=lambda: datetime(2026, 8, 6, 16, 0, tzinfo=timezone.utc),
        )
    assert _snapshot_tree(workspace["trial"]) == before


def test_successor_control_keeps_old_control_historical_and_immutable(
    workspace: dict[str, Path],
) -> None:
    old = _activate(workspace)
    before = _snapshot_tree(old.control_dir)
    (workspace["repository"] / "recovery.txt").write_text("new source\n", encoding="utf-8")
    _git(workspace["repository"], "add", "recovery.txt")
    _git(workspace["repository"], "commit", "-q", "-m", "recovery fixture")
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="Git"):
        _run(workspace, old)
    historical = trial.report_prospective_status(
        control_dir=old.control_dir, trial_root=workspace["trial"],
    )
    assert historical["artifact_integrity"]["status"] == "valid"
    new = _activate(workspace)
    assert new.control_id != old.control_id
    manifest = json.loads((new.control_dir / trial.CONTROL_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    provenance = manifest["identity_material"]["activation_git_provenance"]
    assert provenance["commit_sha"] == _git(workspace["repository"], "rev-parse", "HEAD")
    assert provenance["dirty"] is False
    assert _snapshot_tree(old.control_dir) == before
    assert not (new.control_dir / "prospective_ledger.csv").exists()


def test_missing_game_type_is_excluded_without_network_or_result_access(
    workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _activate(workspace)
    _write_csv(workspace["odds"], baseline.ODDS_REQUIRED_COLUMNS, [_odds_row(event_type="")])

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("prediction accessed a provider or result source")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(baseline, "_load_result_index", forbidden)
    result = _run(workspace, control, dry_run=True)
    assert result.prediction_count == 0
    assert result.exclusions[0]["exclusion_reason"] == baseline.EVENT_TYPE_UNKNOWN_EXCLUSION_REASON
    assert not (control.control_dir / "prospective_ledger.csv").exists()


def test_supported_prediction_cannot_become_betting_or_official_pick(
    workspace: dict[str, Path],
) -> None:
    result = _run(workspace, _activate(workspace), dry_run=True)
    row = result.predictions[0]
    assert row["research_only"] == "true"
    assert row["approval_status"] == "not_approved"
    assert row["eligible_for_betting"] == "false"
    assert row["eligible_for_official_pick"] == "false"


def test_baseline_replay_csv_cannot_be_imported_as_prospective_prediction(
    workspace: dict[str, Path],
) -> None:
    control = _activate(workspace)
    replay = workspace["repository"] / "outputs" / "rehearsal" / "predictions.csv"
    _write_csv(replay, baseline.PREDICTION_COLUMNS, [])
    before = _snapshot_tree(control.control_dir)
    with pytest.raises(trial.MLBHRProspectiveTrialError):
        trial.capture_prospective_closing(
            control_dir=control.control_dir,
            predictions_csv=replay,
            odds_csv=workspace["odds"],
            trial_root=workspace["trial"],
            clock=lambda: NOW,
        )
    assert _snapshot_tree(control.control_dir) == before


def _schedule_event(**overrides: object) -> dict[str, object]:
    return {
        "event_id": "synthetic-event-1",
        "commence_time": "2026-09-15T23:00:00Z",
        "home_team": "Synthetic Home Club",
        "away_team": "Synthetic Away Club",
        "market": "batter_home_runs_alternate",
        "side": "Over",
        "point": "0.5",
        **overrides,
    }


def _schedule_game(event: dict[str, object], **overrides: object) -> dict[str, object]:
    return {
        "gamePk": 10001,
        "gameType": "R",
        "gameDate": event["commence_time"],
        "teams": {
            "home": {"team": {"name": event["home_team"]}},
            "away": {"team": {"name": event["away_team"]}},
        },
        **overrides,
    }


def _enrich_schedule_fixture(
    tmp_path: Path,
    events: list[dict[str, object]],
    games: list[dict[str, object]],
    *,
    require_complete_slate: bool = False,
) -> tuple[dict, list[dict[str, str]]]:
    odds = tmp_path / "synthetic_odds.csv"
    schedule = tmp_path / "synthetic_schedule.json"
    output = tmp_path / "enriched.csv"
    _write_csv(odds, list(events[0]), events)
    schedule.write_text(
        json.dumps({"dates": [{"date": "2026-09-15", "games": games}]}),
        encoding="utf-8",
    )
    original = odds.read_bytes(), schedule.read_bytes()
    try:
        result = recovery.enrich_odds(
            odds_path=odds,
            odds_sha256=hashlib.sha256(original[0]).hexdigest(),
            schedule_path=schedule,
            schedule_sha256=hashlib.sha256(original[1]).hexdigest(),
            operating_date="2026-09-15",
            output=output,
            require_complete_slate=require_complete_slate,
        )
    except recovery.RecoveryContractError:
        assert not output.exists()
        raise
    finally:
        assert (odds.read_bytes(), schedule.read_bytes()) == original
    with output.open(newline="", encoding="utf-8") as stream:
        return result, list(csv.DictReader(stream))


@pytest.mark.parametrize("delta_seconds", [0, 60, -60, 120, -120])
def test_bounded_schedule_drift_preserves_both_time_identities(
    tmp_path: Path, delta_seconds: int,
) -> None:
    official_start = datetime(2026, 9, 15, 23, 0, tzinfo=timezone.utc)
    provider_start = (official_start + timedelta(seconds=delta_seconds)).isoformat()
    event = _schedule_event(commence_time=provider_start)
    result, rows = _enrich_schedule_fixture(
        tmp_path, [event], [_schedule_game(event, gameDate="2026-09-15T23:00:00Z")],
    )
    assert rows[0]["commence_time"] == provider_start
    assert rows[0]["official_commence_time_utc"] == "2026-09-15T23:00:00Z"
    assert float(rows[0]["schedule_start_drift_seconds"]) == delta_seconds
    assert rows[0]["game_pk"] == "10001"
    assert rows[0]["game_type"] == "R"
    assert rows[0]["event_type"] == "regular_season"
    assert {key: result[key] for key in recovery.RESEARCH_FLAGS} == {
        "research_only": True,
        "approval_status": "not_approved",
        "eligible_for_betting": False,
        "eligible_for_official_pick": False,
    }


@pytest.mark.parametrize("delta_seconds", [121, -121])
def test_start_drift_beyond_bound_creates_no_revision(
    tmp_path: Path, delta_seconds: int,
) -> None:
    official_start = datetime(2026, 9, 15, 23, 0, tzinfo=timezone.utc)
    event = _schedule_event(
        commence_time=(official_start + timedelta(seconds=delta_seconds)).isoformat(),
    )
    with pytest.raises(recovery.RecoveryContractError):
        _enrich_schedule_fixture(
            tmp_path, [event], [_schedule_game(event, gameDate=official_start.isoformat())],
        )


@pytest.mark.parametrize("field", ["home_team", "away_team"])
def test_schedule_drift_does_not_relax_exact_team_identity(tmp_path: Path, field: str) -> None:
    event = _schedule_event()
    game = _schedule_game(event, gameDate="2026-09-15T22:59:00Z")
    event[field] = "Different Synthetic Club"
    with pytest.raises(recovery.RecoveryContractError):
        _enrich_schedule_fixture(tmp_path, [event], [game])


@pytest.mark.parametrize("provider_start,official_start", [
    ("2026-09-16T04:00:30Z", "2026-09-16T03:59:30Z"),
    ("2026-09-16T03:59:30Z", "2026-09-16T04:00:30Z"),
])
def test_schedule_drift_cannot_cross_toronto_operating_date(
    tmp_path: Path, provider_start: str, official_start: str,
) -> None:
    event = _schedule_event(commence_time=provider_start)
    with pytest.raises(recovery.RecoveryContractError):
        _enrich_schedule_fixture(tmp_path, [event], [_schedule_game(event, gameDate=official_start)])


def test_bounded_drift_uses_toronto_date_after_utc_midnight(tmp_path: Path) -> None:
    event = _schedule_event(commence_time="2026-09-16T00:01:00Z")
    _, rows = _enrich_schedule_fixture(
        tmp_path, [event], [_schedule_game(event, gameDate="2026-09-16T00:00:00Z")],
    )
    assert float(rows[0]["schedule_start_drift_seconds"]) == 60


def test_official_start_normalizes_to_utc_without_rewriting_provider_offset(tmp_path: Path) -> None:
    event = _schedule_event(commence_time="2026-09-15T19:01:00-04:00")
    _, rows = _enrich_schedule_fixture(
        tmp_path, [event], [_schedule_game(event, gameDate="2026-09-15T19:00:00-04:00")],
    )
    assert rows[0]["commence_time"] == "2026-09-15T19:01:00-04:00"
    assert rows[0]["official_commence_time_utc"] == "2026-09-15T23:00:00Z"
    assert float(rows[0]["schedule_start_drift_seconds"]) == 60


def test_bounded_drift_cannot_admit_a_non_regular_game(tmp_path: Path) -> None:
    event = _schedule_event()
    with pytest.raises(recovery.RecoveryContractError):
        _enrich_schedule_fixture(
            tmp_path, [event], [_schedule_game(event, gameType="P", gameDate="2026-09-15T22:59:00Z")],
        )


def test_schedule_drift_cannot_supply_a_missing_official_game_pk(tmp_path: Path) -> None:
    event = _schedule_event()
    game = _schedule_game(event, gameDate="2026-09-15T22:59:00Z")
    game.pop("gamePk")
    with pytest.raises(recovery.RecoveryContractError):
        _enrich_schedule_fixture(tmp_path, [event], [game])


def test_same_team_doubleheader_maps_each_event_to_its_own_game(tmp_path: Path) -> None:
    first = _schedule_event(commence_time="2026-09-15T17:01:00Z")
    second = _schedule_event(event_id="synthetic-event-2", commence_time="2026-09-15T23:01:00Z")
    _, rows = _enrich_schedule_fixture(tmp_path, [first, second], [
        _schedule_game(first, gameDate="2026-09-15T17:00:00Z"),
        _schedule_game(second, gamePk=10002, gameDate="2026-09-15T23:00:00Z"),
    ])
    assert [(row["event_id"], row["game_pk"]) for row in rows] == [
        ("synthetic-event-1", "10001"), ("synthetic-event-2", "10002"),
    ]


@pytest.mark.parametrize("first_start,second_start", [
    ("2026-09-15T23:00:00Z", "2026-09-15T23:01:00Z"),
    ("2026-09-15T22:59:45Z", "2026-09-15T23:01:30Z"),
    ("2026-09-15T22:59:00Z", "2026-09-15T23:01:00Z"),
])
def test_two_in_window_candidates_never_use_exact_first_or_nearest_guess(
    tmp_path: Path, first_start: str, second_start: str,
) -> None:
    event = _schedule_event()
    with pytest.raises(recovery.RecoveryContractError):
        _enrich_schedule_fixture(tmp_path, [event], [
            _schedule_game(event, gameDate=first_start),
            _schedule_game(event, gamePk=10002, gameDate=second_start),
        ])


@pytest.mark.parametrize("invalid_identity", [{"gameType": "P"}, {"gamePk": None}, {"gamePk": True}])
def test_only_fully_eligible_games_count_as_bounded_candidates(
    tmp_path: Path, invalid_identity: dict[str, object],
) -> None:
    event = _schedule_event()
    _, rows = _enrich_schedule_fixture(tmp_path, [event], [
        _schedule_game(event, gameDate="2026-09-15T22:59:00Z"),
        _schedule_game(event, **{"gamePk": 10002, **invalid_identity}),
    ])
    assert rows[0]["game_pk"] == "10001"


def test_distinct_provider_events_cannot_bind_the_same_official_game(tmp_path: Path) -> None:
    event = _schedule_event()
    second = _schedule_event(event_id="synthetic-event-2", commence_time="2026-09-15T23:01:00Z")
    with pytest.raises(recovery.RecoveryContractError):
        _enrich_schedule_fixture(tmp_path, [event, second], [_schedule_game(event)])


@pytest.mark.parametrize("overrides", [
    {"commence_time": "2026-09-15T23:01:00Z"},
    {"home_team": "Different Synthetic Home Club"},
    {"away_team": "Different Synthetic Away Club"},
])
def test_same_provider_event_cannot_have_inconsistent_identity_rows(
    tmp_path: Path, overrides: dict[str, object],
) -> None:
    event = _schedule_event()
    changed = _schedule_event(**overrides)
    games = [_schedule_game(event)]
    if "commence_time" not in overrides:
        games.append(_schedule_game(changed, gamePk=10002))
    with pytest.raises(recovery.RecoveryContractError):
        _enrich_schedule_fixture(tmp_path, [event, changed], games)


def test_consistent_market_rows_share_one_provider_event_binding(tmp_path: Path) -> None:
    event = _schedule_event()
    _, rows = _enrich_schedule_fixture(
        tmp_path, [event, _schedule_event(market="batter_home_runs")], [_schedule_game(event)],
    )
    assert [row["game_pk"] for row in rows] == ["10001", "10001"]


@pytest.fixture
def synthetic_three_one_minute_drifts() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Reproduce three +60-second conflicts without any preserved provider payload."""
    events = []
    games = []
    for number, hour in enumerate((17, 20, 23), start=1):
        event = _schedule_event(
            event_id=f"synthetic-drift-{number}",
            commence_time=f"2026-09-15T{hour:02d}:01:00Z",
            home_team=f"Synthetic Home Club {number}",
            away_team=f"Synthetic Away Club {number}",
        )
        events.append(event)
        games.append(_schedule_game(
            event, gamePk=20000 + number, gameDate=f"2026-09-15T{hour:02d}:00:00Z",
        ))
    return events, games


def test_three_synthetic_preserved_conflicts_bind_uniquely_at_plus_sixty_seconds(
    tmp_path: Path,
    synthetic_three_one_minute_drifts: tuple[list[dict[str, object]], list[dict[str, object]]],
) -> None:
    events, games = synthetic_three_one_minute_drifts
    result, rows = _enrich_schedule_fixture(tmp_path, events, games, require_complete_slate=True)
    assert len(rows) == 3
    assert len({row["game_pk"] for row in rows}) == 3
    assert [float(row["schedule_start_drift_seconds"]) for row in rows] == [60, 60, 60]
    assert [row["commence_time"] for row in rows] == [event["commence_time"] for event in events]
    assert result["provider_events"] == result["canonical_game_bindings"] == 3
    assert result["eligible_official_games"] == 3
    assert result["ambiguous_bindings"] == result["unmatched_bindings"] == 0
    assert result["match_tolerance_seconds"] == 120
    assert result["complete_slate_required"] is True


def test_complete_slate_requirement_rejects_unbound_official_game(
    tmp_path: Path,
    synthetic_three_one_minute_drifts: tuple[list[dict[str, object]], list[dict[str, object]]],
) -> None:
    events, games = synthetic_three_one_minute_drifts
    with pytest.raises(recovery.RecoveryContractError):
        _enrich_schedule_fixture(tmp_path, events[:2], games, require_complete_slate=True)


def test_default_identity_binding_allows_a_unique_provider_subset(
    tmp_path: Path,
    synthetic_three_one_minute_drifts: tuple[list[dict[str, object]], list[dict[str, object]]],
) -> None:
    events, games = synthetic_three_one_minute_drifts
    _, rows = _enrich_schedule_fixture(tmp_path, events[:2], games)
    assert [row["game_pk"] for row in rows] == ["20001", "20002"]
