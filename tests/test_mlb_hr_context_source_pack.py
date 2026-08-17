from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket

import pytest

from courtvision.sports.mlb.data import context_source_pack as sources
from courtvision.sports.mlb.data.crosswalk_validation import REQUIRED_CROSSWALK_COLUMNS


OPERATING_DATE = "2026-06-05"
CUTOFF = "2026-06-05T18:00:00Z"
ASSEMBLED = "2026-06-05T18:01:00Z"
COMMIT = "a" * 40
GAME_ID = "765432"
BATTER_ID = "600001"
PITCHER_ID = "600009"


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or ()), list(reader)


def _schedule(path: Path, **updates: object) -> Path:
    row: dict[str, object] = {
        "event_id": GAME_ID,
        "operating_date": OPERATING_DATE,
        "commence_time_utc": "2026-06-05T23:00:00Z",
        "home_team": "TOR",
        "away_team": "BOS",
        "venue_id": "1",
        "venue_name": "Rogers Centre",
        "source_record_id": "schedule-game-765432",
        "schedule_snapshot_id": "schedule-2026-06-05-1600z",
        "schedule_snapshot_complete": "true",
        "source_published_or_available_at_utc": "2026-06-05T16:00:00Z",
        "captured_at_utc": "2026-06-05T17:00:00Z",
    }
    row.update(updates)
    return _write_csv(path, tuple(row), [row])


def _roster(path: Path, rows: list[dict[str, object]] | None = None, **updates: object) -> Path:
    row: dict[str, object] = {
        "event_id": GAME_ID,
        "team": "TOR",
        "player_id": BATTER_ID,
        "player_name": "Jose Ramirez Jr.",
        "batter_hand": "R",
        "role": "hitter",
        "eligibility_status": "projected_eligible",
        "source_record_id": "roster-765432-tor-600001",
        "roster_snapshot_id": "rosters-2026-06-05-1630z",
        "team_roster_complete": "true",
        "source_published_or_available_at_utc": "2026-06-05T16:30:00Z",
        "captured_at_utc": "2026-06-05T17:10:00Z",
    }
    row.update(updates)
    opposing_pitcher = {
        **row,
        "team": "BOS",
        "player_id": PITCHER_ID,
        "player_name": "Chris Sale",
        "batter_hand": "L",
        "role": "pitcher",
        "eligibility_status": "active_roster",
        "source_record_id": "roster-765432-bos-600009",
    }
    output_rows = rows if rows is not None else [row, opposing_pitcher]
    return _write_csv(path, tuple(row), output_rows)


def _crosswalk(path: Path, **updates: object) -> Path:
    row: dict[str, object] = {
        "game_date": OPERATING_DATE,
        "retrosheet_game_id": "TOR202606050",
        "mlbam_game_id": GAME_ID,
        "game_number": "1",
        "retrosheet_batter_id": "ramij001",
        "mlbam_batter_id": BATTER_ID,
        "batter_name": "Jose Ramirez Jr.",
        "retrosheet_home_team_id": "TOR",
        "home_team": "TOR",
        "retrosheet_away_team_id": "BOS",
        "away_team": "BOS",
        "retrosheet_batting_team_id": "TOR",
        "batting_team": "TOR",
        "retrosheet_fielding_team_id": "BOS",
        "fielding_team": "BOS",
        "player_mapping_source": "mlbam_registry",
        "game_mapping_source": "mlbam_schedule",
        "team_mapping_source": "canonical_team_table",
        "verified_at": "2026-06-05T15:00:00Z",
        "mlbam_pitcher_id": PITCHER_ID,
        "pitcher_name": "Chris Sale",
        "pitcher_team": "BOS",
        "identity_mapping_version": "mlb-id-map-v2",
    }
    row.update(updates)
    columns = tuple(sorted(REQUIRED_CROSSWALK_COLUMNS)) + (
        "mlbam_pitcher_id",
        "pitcher_name",
        "pitcher_team",
        "identity_mapping_version",
    )
    return _write_csv(path, columns, [row])


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    return (
        _schedule(tmp_path / "schedule.csv"),
        _roster(tmp_path / "roster.csv"),
        _crosswalk(tmp_path / "crosswalk.csv"),
    )


def _candidate_snapshot(tmp_path: Path, inputs: tuple[Path, Path, Path]):
    return sources.collect_candidate_snapshot(
        *inputs,
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        collected_at_utc="2026-06-05T17:15:00Z",
        git_commit=COMMIT,
        research_root=tmp_path / "research",
    )


def _identity_snapshot(tmp_path: Path, crosswalk: Path):
    return sources.collect_identity_snapshot(
        crosswalk,
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        collected_at_utc="2026-06-05T17:16:00Z",
        mapping_source="MLBAM/Retrosheet verified mapping",
        mapping_version="mlb-id-map-v2",
        git_commit=COMMIT,
        research_root=tmp_path / "research",
    )


def _minimal_snapshots(tmp_path: Path, inputs: tuple[Path, Path, Path]) -> dict[str, Path]:
    candidate = _candidate_snapshot(tmp_path, inputs)
    identity = _identity_snapshot(tmp_path, inputs[2])
    return {
        "candidates": candidate.snapshot_dir,
        "identity_crosswalk": identity.snapshot_dir,
    }


def _unavailable() -> dict[str, str]:
    return {
        name: "not collected for this narrow validation pack"
        for name in sources.OPTIONAL_SOURCES
    }


def test_neutral_candidate_contract_and_projected_eligibility(
    inputs: tuple[Path, Path, Path]
) -> None:
    result = sources.build_neutral_candidate_universe(*inputs, cutoff_utc=CUTOFF)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["candidate_universe_origin"] == "neutral_market_independent"
    assert row["candidate_universe_generator"] == sources.CANDIDATE_UNIVERSE_GENERATOR
    assert row["eligibility_basis"] == "projected_eligible"
    assert row["player_id"] == BATTER_ID
    assert row["identity_status"] == "verified_mlbam"
    assert len(result.source_digest) == 64
    assert len(result.configuration_digest) == 64


def test_candidate_generation_is_deterministic(inputs: tuple[Path, Path, Path]) -> None:
    first = sources.build_neutral_candidate_universe(*inputs, cutoff_utc=CUTOFF)
    second = sources.build_neutral_candidate_universe(*inputs, cutoff_utc=CUTOFF)
    assert first == second


def test_market_file_absence_or_presence_cannot_change_candidate_identities(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    first = sources.build_neutral_candidate_universe(*inputs, cutoff_utc=CUTOFF)
    (tmp_path / "unrelated_market.csv").write_text("sportsbook,odds\nBook,200\n")
    second = sources.build_neutral_candidate_universe(*inputs, cutoff_utc=CUTOFF)
    assert [(row["event_id"], row["player_id"]) for row in first.rows] == [
        (row["event_id"], row["player_id"]) for row in second.rows
    ]


@pytest.mark.parametrize("field", sources.MARKET_CONTAMINATION_TOKENS)
def test_candidate_inputs_reject_market_or_model_fields(
    field: str, inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    schedule, roster, crosswalk = inputs
    headers, rows = _read_csv(roster)
    contaminated = tmp_path / f"roster-{field}.csv"
    _write_csv(contaminated, (*headers, field), [{**rows[0], field: "1"}])
    with pytest.raises(sources.ContextSourceError, match="market/model fields"):
        sources.build_neutral_candidate_universe(
            schedule, contaminated, crosswalk, cutoff_utc=CUTOFF
        )


@pytest.mark.parametrize(
    ("target", "updates", "message"),
    [
        ("schedule", {"captured_at_utc": "2026-06-05T18:00:01Z"}, "cutoff"),
        ("roster", {"captured_at_utc": "2026-06-05T18:00:01Z"}, "cutoff"),
        ("schedule", {"commence_time_utc": CUTOFF}, "commence"),
        (
            "roster",
            {
                "source_published_or_available_at_utc": "2026-06-05T17:30:00Z",
                "captured_at_utc": "2026-06-05T17:00:00Z",
            },
            "available",
        ),
    ],
)
def test_candidate_cutoff_enforcement(
    target: str,
    updates: dict[str, object],
    message: str,
    inputs: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    schedule, roster, crosswalk = inputs
    changed = (
        _schedule(tmp_path / "changed-schedule.csv", **updates)
        if target == "schedule"
        else _roster(tmp_path / "changed-roster.csv", **updates)
    )
    with pytest.raises(sources.ContextSourceError, match=message):
        sources.build_neutral_candidate_universe(
            changed if target == "schedule" else schedule,
            changed if target == "roster" else roster,
            crosswalk,
            cutoff_utc=CUTOFF,
        )


def test_schedule_roster_team_mismatch_fails_closed(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    schedule, _, crosswalk = inputs
    roster = _roster(tmp_path / "bad-roster.csv", team="NYY")
    with pytest.raises(sources.ContextSourceError, match="does not match schedule"):
        sources.build_neutral_candidate_universe(schedule, roster, crosswalk, cutoff_utc=CUTOFF)


def test_schedule_snapshot_must_assert_completeness(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    schedule = _schedule(
        tmp_path / "partial-schedule.csv", schedule_snapshot_complete="false"
    )
    with pytest.raises(sources.ContextSourceError, match="schedule snapshot must be complete"):
        sources.build_neutral_candidate_universe(
            schedule, inputs[1], inputs[2], cutoff_utc=CUTOFF
        )


def test_roster_snapshot_must_assert_team_completeness(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    roster = _roster(tmp_path / "partial-roster.csv", team_roster_complete="false")
    with pytest.raises(sources.ContextSourceError, match="roster snapshot must be complete"):
        sources.build_neutral_candidate_universe(
            inputs[0], roster, inputs[2], cutoff_utc=CUTOFF
        )


def test_roster_snapshot_requires_both_scheduled_teams(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    _, rows = _read_csv(inputs[1])
    roster = _roster(tmp_path / "one-team-roster.csv", rows=[rows[0]])
    with pytest.raises(sources.ContextSourceError, match="event/team coverage"):
        sources.build_neutral_candidate_universe(
            inputs[0], roster, inputs[2], cutoff_utc=CUTOFF
        )


def test_missing_crosswalk_mapping_fails_closed(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    schedule, roster, _ = inputs
    crosswalk = _crosswalk(tmp_path / "other-crosswalk.csv", mlbam_batter_id="600002")
    with pytest.raises(sources.ContextSourceError, match="no verified identity"):
        sources.build_neutral_candidate_universe(schedule, roster, crosswalk, cutoff_utc=CUTOFF)


def test_retrosheet_game_alias_is_optional_for_prospective_gamepk(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    schedule, roster, _ = inputs
    crosswalk = _crosswalk(
        tmp_path / "prospective-crosswalk.csv",
        retrosheet_game_id="",
        retrosheet_batter_id="",
    )

    universe = sources.build_neutral_candidate_universe(
        schedule, roster, crosswalk, cutoff_utc=CUTOFF
    )

    assert len(universe.rows) == 1
    assert universe.rows[0]["event_id"] == GAME_ID


def test_conflicting_retrosheet_game_aliases_still_fail_closed(tmp_path: Path) -> None:
    crosswalk = _crosswalk(tmp_path / "conflicting-alias.csv")
    columns, rows = _read_csv(crosswalk)
    second = {
        **rows[0],
        "mlbam_batter_id": "600002",
        "batter_name": "Another Batter",
        "retrosheet_batter_id": "batta002",
        "retrosheet_game_id": "TOR202606051",
    }
    _write_csv(crosswalk, columns, [rows[0], second])

    with pytest.raises(sources.ContextSourceError, match="conflicting MLBAM-to-Retrosheet"):
        sources.build_neutral_candidate_universe(
            _schedule(tmp_path / "schedule.csv"),
            _roster(tmp_path / "roster.csv"),
            crosswalk,
            cutoff_utc=CUTOFF,
        )


def test_roster_crosswalk_name_mismatch_fails_closed(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    schedule, roster, _ = inputs
    crosswalk = _crosswalk(tmp_path / "bad-name.csv", batter_name="Different Person")
    with pytest.raises(sources.ContextSourceError, match="identity mismatch"):
        sources.build_neutral_candidate_universe(schedule, roster, crosswalk, cutoff_utc=CUTOFF)


def test_ineligible_and_pitcher_rows_do_not_enter_universe(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    schedule, _, crosswalk = inputs
    _, base_rows = _read_csv(inputs[1])
    pitcher = {**base_rows[0], "role": "pitcher"}
    inactive = {
        **base_rows[0],
        "player_id": "600002",
        "eligibility_status": "inactive",
        "source_record_id": "roster-765432-tor-600002",
    }
    roster = _roster(
        tmp_path / "filtered.csv", rows=[pitcher, inactive, base_rows[1]]
    )
    with pytest.raises(sources.ContextSourceError, match="no eligible hitters"):
        sources.build_neutral_candidate_universe(schedule, roster, crosswalk, cutoff_utc=CUTOFF)


def test_candidate_source_digest_binds_identity_input(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    first = sources.build_neutral_candidate_universe(*inputs, cutoff_utc=CUTOFF)
    changed_crosswalk = _crosswalk(
        tmp_path / "changed-crosswalk.csv", player_mapping_source="mlbam_registry_v2"
    )
    second = sources.build_neutral_candidate_universe(
        inputs[0], inputs[1], changed_crosswalk, cutoff_utc=CUTOFF
    )
    assert first.source_digest != second.source_digest
    assert first.candidate_universe_id != second.candidate_universe_id


def test_deterministic_snapshot_id_across_roots(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    first = sources.collect_candidate_snapshot(
        *inputs,
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        collected_at_utc="2026-06-05T17:15:00Z",
        git_commit=COMMIT,
        research_root=tmp_path / "a",
    )
    second = sources.collect_candidate_snapshot(
        *inputs,
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        collected_at_utc="2026-06-05T17:15:00Z",
        git_commit=COMMIT,
        research_root=tmp_path / "b",
    )
    assert first.snapshot_id == second.snapshot_id
    assert first.data_path.read_bytes() == second.data_path.read_bytes()
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()


def test_snapshot_overwrite_is_refused(inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    _candidate_snapshot(tmp_path, inputs)
    with pytest.raises(FileExistsError, match="immutable source snapshot"):
        _candidate_snapshot(tmp_path, inputs)


def test_snapshot_manifest_binds_raw_inputs(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    snapshot = _candidate_snapshot(tmp_path, inputs)
    manifest = json.loads(snapshot.manifest_path.read_text(encoding="utf-8"))
    assert manifest["sha256"] == hashlib.sha256(snapshot.data_path.read_bytes()).hexdigest()
    assert {item["path"] for item in manifest["raw_inputs"]} == {
        "raw/identity_crosswalk.csv",
        "raw/roster.csv",
        "raw/schedule.csv",
    }
    assert all(len(item["sha256"]) == 64 for item in manifest["raw_inputs"])
    assert manifest["model_training_enabled"] is False
    assert manifest["predictions_enabled"] is False


def test_snapshot_collection_after_cutoff_is_rejected(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    with pytest.raises(sources.ContextSourceError, match="after request cutoff"):
        sources.collect_candidate_snapshot(
            *inputs,
            operating_date=OPERATING_DATE,
            cutoff_utc=CUTOFF,
            collected_at_utc="2026-06-05T18:00:01Z",
            git_commit=COMMIT,
            research_root=tmp_path / "research",
        )


def test_prospective_trial_namespace_is_rejected(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    root = tmp_path / "outputs" / "research" / "mlb_hr_prospective_trial"
    with pytest.raises(sources.ContextSourceError, match="prospective-trial"):
        sources.collect_candidate_snapshot(
            *inputs,
            operating_date=OPERATING_DATE,
            cutoff_utc=CUTOFF,
            collected_at_utc="2026-06-05T17:15:00Z",
            git_commit=COMMIT,
            research_root=root,
        )


def test_identity_snapshot_rejects_version_mismatch(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    with pytest.raises(sources.ContextSourceError, match="mapping version mismatch"):
        sources.collect_identity_snapshot(
            inputs[2],
            operating_date=OPERATING_DATE,
            cutoff_utc=CUTOFF,
            collected_at_utc="2026-06-05T17:16:00Z",
            mapping_source="verified",
            mapping_version="different",
            git_commit=COMMIT,
            research_root=tmp_path / "research",
        )


def test_identity_snapshot_rejects_future_verification(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    crosswalk = _crosswalk(tmp_path / "future.csv", verified_at="2026-06-05T18:00:01Z")
    with pytest.raises(sources.ContextSourceError, match="verified after cutoff"):
        sources.collect_identity_snapshot(
            crosswalk,
            operating_date=OPERATING_DATE,
            cutoff_utc=CUTOFF,
            collected_at_utc="2026-06-05T17:16:00Z",
            mapping_source="verified",
            mapping_version="mlb-id-map-v2",
            git_commit=COMMIT,
            research_root=tmp_path / "research",
        )


def _statcast(path: Path, rows: list[dict[str, object]] | None = None) -> Path:
    base: dict[str, object] = {
        "game_pk": "765400",
        "game_date": "2026-06-01",
        "at_bat_number": "1",
        "pitch_number": "1",
        "batter": BATTER_ID,
        "pitcher": PITCHER_ID,
        "stand": "R",
        "p_throws": "L",
        "home_team": "TOR",
        "away_team": "BOS",
        "inning": "1",
        "inning_topbot": "Bot",
        "events": "home_run",
        "description": "hit_into_play",
        "pitch_type": "FF",
        "release_speed": "95.1",
        "launch_speed": "102.2",
        "launch_angle": "27",
        "bb_type": "fly_ball",
        "barrel": "1",
        "estimated_woba_using_speedangle": "0.91",
        "estimated_slg_using_speedangle": "1.9",
        "sv_id": "pitch-1",
    }
    return _write_csv(path, tuple(base), rows or [base])


def _game_clocks(path: Path, **updates: object) -> Path:
    row: dict[str, object] = {
        "game_id": "765400",
        "game_completed_at_utc": "2026-06-01T22:00:00Z",
        "provider_published_at_utc": "2026-06-01T22:05:00Z",
        "first_observed_at_utc": "2026-06-01T22:06:00Z",
        "captured_at_utc": "2026-06-01T22:07:00Z",
    }
    row.update(updates)
    return _write_csv(path, tuple(row), [row])


def test_statcast_adapter_preserves_pitch_grain_and_raw_metrics(tmp_path: Path) -> None:
    rows = sources.normalize_statcast_pitch_csv(
        _statcast(tmp_path / "statcast.csv"),
        _game_clocks(tmp_path / "clocks.csv"),
        captured_at_utc="2026-06-01T22:10:00Z",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["plate_appearance_id"] == "765400:1"
    assert row["pitch_id"] == "pitch-1"
    assert row["is_terminal_pa"] == "true"
    assert row["is_home_run"] == "true"
    assert row["batter_team"] == "TOR"
    assert row["pitcher_team"] == "BOS"
    assert row["estimated_woba"] == "0.91"
    assert row["is_pull"] == ""


def test_statcast_uses_first_observed_when_provider_publication_is_absent(
    tmp_path: Path,
) -> None:
    rows = sources.normalize_statcast_pitch_csv(
        _statcast(tmp_path / "statcast.csv"),
        _game_clocks(
            tmp_path / "clocks.csv",
            provider_published_at_utc="",
        ),
        captured_at_utc="2026-06-01T22:10:00Z",
    )

    assert rows[0]["provider_published_at_utc"] == ""
    assert rows[0]["first_observed_at_utc"] == "2026-06-01T22:06:00Z"
    assert rows[0]["captured_at_utc"] == "2026-06-01T22:07:00Z"


def test_statcast_capture_after_requested_cutoff_cannot_reconstruct_history(
    tmp_path: Path,
) -> None:
    with pytest.raises(sources.ContextSourceError, match="after cutoff"):
        sources.collect_statcast_snapshot(
            _statcast(tmp_path / "statcast.csv"),
            _game_clocks(
                tmp_path / "clocks.csv",
                provider_published_at_utc="",
                first_observed_at_utc="2026-06-01T22:09:00Z",
                captured_at_utc="2026-06-01T22:11:00Z",
            ),
            operating_date=OPERATING_DATE,
            cutoff_utc="2026-06-01T22:10:00Z",
            captured_at_utc="2026-06-01T22:11:00Z",
            git_commit=COMMIT,
            research_root=tmp_path / "research",
        )


def test_statcast_adapter_accepts_multi_pitch_pa_with_one_terminal(tmp_path: Path) -> None:
    path = _statcast(tmp_path / "statcast.csv")
    columns, rows = _read_csv(path)
    first = {**rows[0], "events": "", "pitch_number": "1", "sv_id": "p1"}
    terminal = {**rows[0], "pitch_number": "2", "sv_id": "p2"}
    _write_csv(path, columns, [first, terminal])
    normalized = sources.normalize_statcast_pitch_csv(
        path,
        _game_clocks(tmp_path / "clocks.csv"),
        captured_at_utc="2026-06-01T22:10:00Z",
    )
    assert [row["pitch_number"] for row in normalized] == [1, 2]
    assert [row["is_terminal_pa"] for row in normalized] == ["false", "true"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate", "duplicate Statcast pitch identity"),
        ("terminal_first", "not the final pitch"),
    ],
)
def test_statcast_pitch_identity_and_terminal_rules(
    mutation: str, message: str, tmp_path: Path
) -> None:
    path = _statcast(tmp_path / "statcast.csv")
    columns, rows = _read_csv(path)
    if mutation == "duplicate":
        changed = [rows[0], rows[0]]
    elif mutation == "no_terminal":
        changed = [{**rows[0], "events": ""}]
    else:
        changed = [rows[0], {**rows[0], "events": "", "pitch_number": "2", "sv_id": "p2"}]
    _write_csv(path, columns, changed)
    with pytest.raises(sources.ContextSourceError, match=message):
        sources.normalize_statcast_pitch_csv(
            path,
            _game_clocks(tmp_path / "clocks.csv"),
            captured_at_utc="2026-06-01T22:10:00Z",
        )


def test_statcast_requires_separate_game_completion_evidence(tmp_path: Path) -> None:
    clocks = _game_clocks(tmp_path / "clocks.csv", game_id="765401")
    with pytest.raises(sources.ContextSourceError, match="no verified game-completion clock"):
        sources.normalize_statcast_pitch_csv(
            _statcast(tmp_path / "statcast.csv"),
            clocks,
            captured_at_utc="2026-06-01T22:10:00Z",
        )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {"game_completed_at_utc": "2026-06-01T22:06:00Z"},
            "exact completion occurs after trustworthy Statcast availability",
        ),
        (
            {"provider_published_at_utc": "2026-06-01T22:07:00Z"},
            "availability <= first_observed",
        ),
        (
            {"first_observed_at_utc": "2026-06-01T22:08:00Z"},
            "first_observed <= captured",
        ),
    ],
)
def test_statcast_clock_ordering(updates: dict[str, object], message: str, tmp_path: Path) -> None:
    with pytest.raises(sources.ContextSourceError, match=message):
        sources.normalize_statcast_pitch_csv(
            _statcast(tmp_path / "statcast.csv"),
            _game_clocks(tmp_path / "clocks.csv", **updates),
            captured_at_utc="2026-06-01T22:10:00Z",
        )


def _normalized_csv(source_name: str, path: Path, **updates: object) -> Path:
    rows: dict[str, dict[str, object]] = {
        "probable_pitchers": {
            "event_id": GAME_ID,
            "team": "BOS",
            "pitcher_id": PITCHER_ID,
            "pitcher_name": "Chris Sale",
            "normalized_pitcher_name": "christopher sale",
            "pitcher_hand": "L",
            "probable_pitcher_status": "confirmed",
            "identity_status": "verified_mlbam",
            "identity_mapping_version": "mlb-id-map-v2",
            "provider_published_at_utc": "2026-06-05T17:00:00Z",
            "first_observed_at_utc": "2026-06-05T17:02:00Z",
            "captured_at_utc": "2026-06-05T17:05:00Z",
            "source": "MLB StatsAPI",
            "source_record_id": "game-765432-away-probable",
            "source_version": "statsapi-v1",
        },
        "lineups": {
            "event_id": GAME_ID,
            "team": "TOR",
            "player_id": BATTER_ID,
            "lineup_status": "confirmed",
            "batting_order_position": "2",
            "provider_published_at_utc": "2026-06-05T17:00:00Z",
            "first_observed_at_utc": "2026-06-05T17:02:00Z",
            "captured_at_utc": "2026-06-05T17:05:00Z",
            "source": "MLB StatsAPI",
            "source_record_id": "game-765432-tor-lineup-600001",
        },
        "weather": {
            "event_id": GAME_ID,
            "venue_id": "1",
            "venue_name": "Rogers Centre",
            "weather_type": "forecast",
            "weather_evidence_class": "provider_pregame_forecast",
            "issued_at_utc": "2026-06-05T17:00:00Z",
            "valid_for_utc": "2026-06-05T23:00:00Z",
            "measured_at_utc": "",
            "captured_at_utc": "2026-06-05T17:05:00Z",
            "temperature": "22",
            "temperature_unit": "celsius",
            "wind_speed": "10",
            "wind_speed_unit": "kmh",
            "wind_direction": "out_to_left",
            "humidity": "58",
            "roof_status": "open",
            "precipitation": "0",
            "source": "pregame forecast provider",
            "source_record_id": "forecast-765432-1700z",
            "source_version": "forecast-v1",
        },
        "park_factors": {
            "venue_id": "1",
            "venue_name": "Rogers Centre",
            "park_hr_factor": "1.08",
            "park_factor_source": "supplied published table",
            "park_factor_version": "2026-v1",
            "effective_from_date": "2026-01-01",
            "effective_to_date": "2026-12-31",
            "published_or_available_at_utc": "2026-03-01T12:00:00Z",
            "captured_at_utc": "2026-03-01T12:05:00Z",
            "factor_type": "home_run",
            "factor_value": "1.08",
            "source_record_id": "park-1-2026-v1-home-run",
        },
        "market": {
            "event_id": GAME_ID,
            "player_id": BATTER_ID,
            "team": "TOR",
            "sportsbook": "Book A",
            "american_odds": "+250",
            "evidence_class": "pregame_snapshot",
            "market_configuration_id": "hr-over-0.5-us-v1",
            "source_snapshot_id": "odds-api-run-1",
            "source_record_id": "odds-api-run-1-book-a-600001",
            "quote_at_utc": "2026-06-05T17:00:00Z",
            "captured_at_utc": "2026-06-05T17:05:00Z",
        },
    }
    row = rows[source_name]
    row.update(updates)
    return _write_csv(path, tuple(row), [row])


@pytest.mark.parametrize(
    ("source_name", "updates", "message"),
    [
        (
            "probable_pitchers",
            {"provider_published_at_utc": "2026-06-05T17:03:00Z"},
            "trustworthy availability",
        ),
        ("probable_pitchers", {"source_version": ""}, "source_version"),
        ("lineups", {"batting_order_position": "10"}, "1 through 9"),
        ("lineups", {"lineup_status": "roster_only"}, "lineup_status is unsupported"),
        ("lineups", {"expected_pa": "4.5"}, "expected_pa requires source and version"),
        ("weather", {"weather_evidence_class": "final_game_weather"}, "final observed"),
        ("weather", {"issued_at_utc": "2026-06-05T17:06:00Z"}, "source time"),
        ("weather", {"humidity": "101"}, "humidity must be between 0 and 100"),
        (
            "weather",
            {"temperature_unit": ""},
            "temperature and temperature_unit must be supplied together",
        ),
        ("park_factors", {"effective_to_date": "2025-12-31"}, "effective interval"),
        ("park_factors", {"factor_value": "1.09"}, "must equal park_hr_factor"),
        ("market", {"evidence_class": "closing_snapshot"}, "closing/non-pregame"),
        ("market", {"quote_at_utc": "2026-06-05T17:06:00Z"}, "source time"),
        ("market", {"player_id": "Jose Ramirez"}, "canonical MLBAM"),
        ("market", {"source_snapshot_id": ""}, "source_snapshot_id"),
    ],
)
def test_normalized_adapter_boundaries_fail_closed(
    source_name: str,
    updates: dict[str, object],
    message: str,
    tmp_path: Path,
) -> None:
    input_csv = _normalized_csv(source_name, tmp_path / f"{source_name}.csv", **updates)
    with pytest.raises(sources.ContextSourceError, match=message):
        sources.collect_normalized_source_snapshot(
            source_name,
            input_csv,
            operating_date=OPERATING_DATE,
            cutoff_utc=CUTOFF,
            collected_at_utc="2026-06-05T17:10:00Z",
            provider="test provider",
            collector_configuration={"version": "v1"},
            git_commit=COMMIT,
            research_root=tmp_path / "research",
        )


def test_market_snapshot_keeps_exact_id_and_book_lineage(tmp_path: Path) -> None:
    input_csv = _normalized_csv("market", tmp_path / "market.csv")
    snapshot = sources.collect_normalized_source_snapshot(
        "market",
        input_csv,
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        collected_at_utc="2026-06-05T17:10:00Z",
        provider="The Odds API normalized export",
        collector_configuration={"version": "v1"},
        git_commit=COMMIT,
        research_root=tmp_path / "research",
    )
    _, rows = _read_csv(snapshot.data_path)
    assert rows[0]["event_id"] == GAME_ID
    assert rows[0]["player_id"] == BATTER_ID
    assert rows[0]["sportsbook"] == "Book A"
    assert rows[0]["source_snapshot_id"] == "odds-api-run-1"


def test_lineup_snapshot_preserves_source_supplied_expected_pa(tmp_path: Path) -> None:
    input_csv = _normalized_csv(
        "lineups",
        tmp_path / "lineups.csv",
        expected_pa="4.5",
        expected_pa_source="pregame projection provider",
        expected_pa_version="expected-pa-v1",
    )
    snapshot = sources.collect_normalized_source_snapshot(
        "lineups",
        input_csv,
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        collected_at_utc="2026-06-05T17:10:00Z",
        provider="pregame lineup provider",
        collector_configuration={"version": "v1"},
        git_commit=COMMIT,
        research_root=tmp_path / "research",
    )

    _, rows = _read_csv(snapshot.data_path)

    assert rows[0]["expected_pa"] == "4.5"
    assert rows[0]["expected_pa_source"] == "pregame projection provider"
    assert rows[0]["expected_pa_version"] == "expected-pa-v1"


def test_partial_source_status_is_bound_into_pack_manifest(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    lineup = sources.collect_normalized_source_snapshot(
        "lineups",
        _normalized_csv(
            "lineups",
            tmp_path / "lineups.csv",
            provider_published_at_utc="",
        ),
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        collected_at_utc="2026-06-05T17:10:00Z",
        provider="persisted test observation",
        collector_configuration={"version": "v1"},
        git_commit=COMMIT,
        research_root=tmp_path / "research",
        availability_status="partial",
        availability_note="one of two team lineups observed",
    )
    snapshots = _minimal_snapshots(tmp_path, inputs)
    snapshots["lineups"] = lineup.snapshot_dir
    unavailable = _unavailable()
    unavailable.pop("lineups")
    pack = sources.assemble_context_source_pack(
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        assembled_at_utc=ASSEMBLED,
        snapshot_dirs=snapshots,
        unavailable_sources=unavailable,
        git_commit=COMMIT,
        research_root=tmp_path / "research",
    )
    manifest = json.loads(pack.manifest_path.read_text(encoding="utf-8"))
    entry = next(
        item for item in manifest["source_files"] if item["source_name"] == "lineups"
    )

    assert entry["available"] is True
    assert entry["availability_status"] == "partial"
    assert entry["availability_note"] == "one of two team lineups observed"


def test_assemble_requires_explicit_optional_source_reasons(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    snapshots = _minimal_snapshots(tmp_path, inputs)
    with pytest.raises(sources.ContextSourceError, match="explicit unavailable reason"):
        sources.assemble_context_source_pack(
            operating_date=OPERATING_DATE,
            cutoff_utc=CUTOFF,
            assembled_at_utc=ASSEMBLED,
            snapshot_dirs=snapshots,
            unavailable_sources={},
            git_commit=COMMIT,
            research_root=tmp_path / "research",
        )


def test_valid_minimal_pack_is_feature_store_v2_compatible(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    pack = sources.assemble_context_source_pack(
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        assembled_at_utc=ASSEMBLED,
        snapshot_dirs=_minimal_snapshots(tmp_path, inputs),
        unavailable_sources=_unavailable(),
        git_commit=COMMIT,
        research_root=tmp_path / "research",
    )
    validation = sources.validate_context_source_pack(pack.pack_dir)
    assert validation.is_valid
    assert validation.errors == ()
    assert validation.feature_row_count == 1
    manifest = json.loads(pack.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == sources.SOURCE_PACK_SCHEMA_VERSION
    assert manifest["feature_schema_version"] == "mlb-hr-context-feature-v2"
    assert manifest["model_training_enabled"] is False
    assert manifest["official_pick_or_lifecycle_modified"] is False
    assert sum(item["available"] is False for item in manifest["source_files"]) == 6


def test_pack_id_and_manifest_are_deterministic_across_roots(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    packs = []
    for root_name in ("one", "two"):
        root = tmp_path / root_name
        pack = sources.assemble_context_source_pack(
            operating_date=OPERATING_DATE,
            cutoff_utc=CUTOFF,
            assembled_at_utc=ASSEMBLED,
            snapshot_dirs=_minimal_snapshots(root, inputs),
            unavailable_sources=_unavailable(),
            git_commit=COMMIT,
            research_root=root / "research",
        )
        packs.append(pack)
    assert packs[0].pack_id == packs[1].pack_id
    assert packs[0].manifest_path.read_bytes() == packs[1].manifest_path.read_bytes()


def test_pack_overwrite_is_refused(inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    snapshots = _minimal_snapshots(tmp_path, inputs)
    arguments = dict(
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        assembled_at_utc=ASSEMBLED,
        snapshot_dirs=snapshots,
        unavailable_sources=_unavailable(),
        git_commit=COMMIT,
        research_root=tmp_path / "research",
    )
    sources.assemble_context_source_pack(**arguments)
    with pytest.raises(FileExistsError, match="immutable source pack"):
        sources.assemble_context_source_pack(**arguments)


def test_pack_validation_detects_source_mutation(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    pack = sources.assemble_context_source_pack(
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        assembled_at_utc=ASSEMBLED,
        snapshot_dirs=_minimal_snapshots(tmp_path, inputs),
        unavailable_sources=_unavailable(),
        git_commit=COMMIT,
        research_root=tmp_path / "research",
    )
    (pack.pack_dir / sources.SOURCE_FILES["candidates"]).write_bytes(b"changed")
    validation = sources.validate_context_source_pack(pack.pack_dir)
    assert not validation.is_valid
    assert any("mutation/digest mismatch" in error for error in validation.errors)


def test_pack_validation_rejects_unbound_csv(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    pack = sources.assemble_context_source_pack(
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        assembled_at_utc=ASSEMBLED,
        snapshot_dirs=_minimal_snapshots(tmp_path, inputs),
        unavailable_sources=_unavailable(),
        git_commit=COMMIT,
        research_root=tmp_path / "research",
    )
    (pack.pack_dir / "unbound.csv").write_text("x\n1\n", encoding="utf-8")
    validation = sources.validate_context_source_pack(pack.pack_dir)
    assert not validation.is_valid
    assert any("unbound CSV" in error for error in validation.errors)


def test_pack_assembly_and_validation_are_offline(
    inputs: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", no_network)
    pack = sources.assemble_context_source_pack(
        operating_date=OPERATING_DATE,
        cutoff_utc=CUTOFF,
        assembled_at_utc=ASSEMBLED,
        snapshot_dirs=_minimal_snapshots(tmp_path, inputs),
        unavailable_sources=_unavailable(),
        git_commit=COMMIT,
        research_root=tmp_path / "research",
    )
    assert sources.validate_context_source_pack(pack.pack_dir).is_valid


def test_source_module_has_no_training_or_operational_imports() -> None:
    text = Path(sources.__file__).read_text(encoding="utf-8")
    assert "OfficialPick" not in text
    assert "settle_official" not in text
    assert "kelly" not in text.casefold().replace('"kelly_eligible"', "")
    assert "train_model" not in text
    assert "urllib.request" not in text
    assert "requests.get" not in text
def test_statcast_provider_az_team_alias_normalizes_to_ari_and_unknown_still_fails() -> None:
    assert sources._team("AZ", "team") == "ARI"
    assert sources._team("az", "team") == "ARI"
    assert sources._team(" ARI ", "team") == "ARI"

    with pytest.raises(
        sources.ContextSourceError,
        match="canonical MLB team",
    ):
        sources._team("ZZZ", "team")
