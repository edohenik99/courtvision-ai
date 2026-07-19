from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from courtvision.sports.nba.player_points_rehearsal import (
    NBAPlayerPointsRehearsalError,
    build_rehearsal_fixture_bundle,
    run_nba_player_points_rehearsal,
)


REHEARSAL_MODULE = (
    Path(__file__).resolve().parents[1]
    / "courtvision"
    / "sports"
    / "nba"
    / "player_points_rehearsal.py"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root: Path) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        sorted(
            (str(path.relative_to(root)), _sha256(path), path.stat().st_size)
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def test_fixture_bundle_shape_and_architectural_boundary() -> None:
    bundle = build_rehearsal_fixture_bundle()
    payload = bundle.to_dict()
    text = json.dumps(payload, sort_keys=True).casefold()
    source_text = REHEARSAL_MODULE.read_text(encoding="utf-8").casefold()

    assert len(payload["canonical_schedule_rows"]) == 2
    assert len(payload["canonical_player_rows"]) >= 4
    assert len({row["sportsbook"] for row in payload["market_rows"]}) >= 2
    assert len({row["line"] for row in payload["market_rows"]}) > 2
    assert "credential" not in text
    assert "secret" not in text
    assert "api_key" not in text
    assert "sports.mlb" not in source_text
    assert "run_today" not in source_text
    assert "data/history" not in source_text
    assert "outputs/runtime" not in source_text


def test_complete_offline_rehearsal_flow_and_pregame_buckets() -> None:
    result = run_nba_player_points_rehearsal()
    summary = result.rehearsal_summary
    integrity = result.integrity_report

    assert integrity["violations"] == ()
    assert integrity["all_timestamps_utc"] is True
    assert integrity["no_target_actual_points_in_pregame"] is True
    assert integrity["no_target_actual_minutes_in_pregame"] is True
    assert integrity["schema_versions_supported"] is True
    assert integrity["input_fixtures_unchanged"] is True
    assert integrity["event_conflicts_detected"] == 2

    assert dict(summary["pregame_bucket_counts"]) == {
        "total_rows": 11,
        "eligible_projection_rows": 4,
        "eligible_probability_rows": 1,
        "excluded_rows": 4,
        "quarantined_rows": 1,
        "conflicting_rows": 2,
        "duplicate_diagnostics": 2,
    }
    assert summary["no_probability_fabrication"] is True
    assert summary["directional_diagnostics_non_betting"] is True
    assert summary["no_official_selection_fields"] is True

    probability_rows = [
        row for row in result.pregame_rows if row["probability_research_eligible"]
    ]
    projection_only_rows = [
        row
        for row in result.pregame_rows
        if row["projection_research_eligible"] and not row["probability_research_eligible"]
    ]
    assert [row["player_name"] for row in probability_rows] == ["Tyrese Haliburton"]
    assert {row["probability_status"] for row in projection_only_rows} == {"unavailable"}
    assert any(row["player_name"] == "Mystery Laker" for row in summary["unresolved_assembly_rows"])
    assert any(row["player_name"] == "Austin Reaves" for row in result.pregame_rows if row["assembly_status"] == "quarantined")


def test_duplicate_conflict_settlement_and_hashing_outcomes() -> None:
    result = run_nba_player_points_rehearsal()
    summary = result.rehearsal_summary

    duplicate_statuses = {
        diagnostic["duplicate_status"] for diagnostic in summary["duplicate_outcomes"]
    }
    assert duplicate_statuses == {"identical_collapsed", "conflicting"}
    assert dict(summary["settlement_counts"]) == {
        "conflicting": 1,
        "manual_review_required": 1,
        "settled": 2,
        "unresolved": 1,
        "void": 1,
    }
    assert len(summary["missing_minutes_outcomes"]) == 1
    assert summary["missing_minutes_outcomes"][0]["actual_minutes"] is None
    assert summary["dnp_outcomes"][0]["settlement_status"] == "void"
    assert summary["unresolved_settlement_rows"][0]["settlement_status"] == "unresolved"
    assert result.settlement_diagnostics["conflicting"][0]["conflict_reason"] == "conflicting_final_points"
    assert "duplicate_identical_replay" in result.settlement_diagnostics

    assert all(summary["prediction_immutability"].values())
    assert all(summary["hashing"].values())
    assert summary["fixture_immutability"]["unchanged"] is True


def test_no_live_calls_credentials_or_production_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    with (
        patch("requests.Session.get", side_effect=AssertionError("live call attempted")) as mock_get,
        patch("os.getenv", side_effect=AssertionError("credential read attempted")) as mock_getenv,
    ):
        result = run_nba_player_points_rehearsal()

    assert result.rehearsal_summary["pregame_bucket_counts"]["total_rows"] == 11
    assert mock_get.call_count == 0
    assert mock_getenv.call_count == 0
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "data" / "history").exists()
    assert not (tmp_path / "test_outputs").exists()


def test_temp_preview_outputs_are_explicit_and_limited(tmp_path: Path) -> None:
    preview_dir = tmp_path / "previews"
    result = run_nba_player_points_rehearsal(preview_output_dir=preview_dir)

    assert {Path(path).name for path in result.preview_paths} == {
        "pregame_rows.json",
        "pregame_manifest.json",
        "settlement_preview.json",
        "integrity_report.json",
        "rehearsal_summary.json",
    }
    for path in result.preview_paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        assert payload
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "data" / "history").exists()


def test_preview_output_rejects_non_temp_destinations() -> None:
    with pytest.raises(NBAPlayerPointsRehearsalError, match="system temp"):
        run_nba_player_points_rehearsal(preview_output_dir=Path.cwd() / "rehearsal-preview")
