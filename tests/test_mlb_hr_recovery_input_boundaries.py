"""Offline regression fixtures for the new-control recovery input boundary."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import socket

import pytest

from courtvision.sports.mlb.training import hr_prospective_trial as trial
from courtvision.sports.mlb.training import hr_research_baseline as baseline
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
