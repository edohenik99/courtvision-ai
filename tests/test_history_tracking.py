from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts.dashboard import load_dashboard_data
from scripts.history_tracking import (
    grade_completed_picks,
    migrate_pick_history_schema,
    persist_daily_picks,
    update_performance_summaries,
    PICK_HISTORY_COLUMNS,
)


def _write_elite_board(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_audit(path: Path, max_team: int = 2, max_game: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "summary": {
                    "provider_used": "test_provider",
                    "board_analytics": {
                        "max_team_exposure": max_team,
                        "max_game_exposure": max_game,
                    },
                },
                "totals": {"total_candidates": 2, "total_rejections": 0},
            }
        ),
        encoding="utf-8",
    )


def _history_row(
    player_name: str,
    *,
    prediction_date: str = "2026-04-01",
    market: str = "player_points",
    selection: str = "over",
    result_status: str = "hit",
    edge: float = 1.5,
    confidence: str = "0.75",
    kelly_eligible: str = "",
    skip_reason: str = "",
    context_caution_level: str = "",
    context_pick_alignment: str = "",
    line_source: str = "",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "run_timestamp": f"{prediction_date}T12:00:00+00:00",
        "player_name": player_name,
        "team": "BOS",
        "opponent": "NYK",
        "game_id": "",
        "market": market,
        "selection": selection,
        "line": 20.5,
        "projection": 22.0,
        "edge": edge,
        "abs_edge": abs(edge),
        "odds": "-110",
        "confidence": confidence,
        "quality_score": 80.0,
        "qualification_reason": "pass",
        "provider_used": "test",
        "result_status": result_status,
        "kelly_eligible": kelly_eligible,
        "skip_reason": skip_reason,
        "context_caution_level": context_caution_level,
        "context_pick_alignment": context_pick_alignment,
        "line_source": line_source,
    }


def _cross_row(cross: pd.DataFrame, dimension: str, group_value: str) -> pd.Series:
    match = cross[(cross["dimension"] == dimension) & (cross["group_value"].astype(str) == group_value)]
    assert len(match) == 1, f"expected one row for {dimension}={group_value}"
    return match.iloc[0]


def _bool_value(value: object) -> bool:
    return str(value).strip().lower() == "true"


def test_pick_history_append_works(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-04-23"
    _write_elite_board(
        runtime_root / "operator" / f"elite_board_{date}.csv",
        [
            {
                "prediction_date": date,
                "player_name": "Alpha Star",
                "team": "BOS",
                "opponent": "NYK",
                "game_id": 101,
                "market_type": "player_points",
                "selection": "over",
                "sportsbook_line": 24.5,
                "model_projection": 27.0,
                "edge": 2.5,
                "odds": -110,
                "confidence": 0.7,
                "quality_score": 80.0,
                "qualification_reason": "pass",
            }
        ],
    )
    _write_audit(runtime_root / "operator" / f"elite_pipeline_audit_summary_{date}.json")

    result = persist_daily_picks(prediction_date=date, runtime_root=runtime_root, history_root=history_root)
    assert result["appended_rows"] == 1
    assert (runtime_root / "history" / f"picks_{date}.csv").exists()

    history = pd.read_csv(history_root / "pick_history.csv")
    assert len(history) == 1
    assert history.iloc[0]["result_status"] == "pending"
    assert history.iloc[0]["provider_used"] == "test_provider"


def test_pending_picks_do_not_crash_grading(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    history_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "prediction_date": "2026-04-24",
                "run_timestamp": "2026-04-24T12:00:00+00:00",
                "player_name": "No Result",
                "team": "BOS",
                "opponent": "NYK",
                "game_id": "",
                "market": "points",
                "selection": "over",
                "line": 20.5,
                "projection": 23.0,
                "edge": 2.5,
                "abs_edge": 2.5,
                "odds": "-110",
                "confidence": "0.7",
                "quality_score": 70.0,
                "qualification_reason": "pass",
                "provider_used": "test",
                "result_status": "pending",
            }
        ]
    ).to_csv(history_root / "pick_history.csv", index=False)

    result = grade_completed_picks(history_root=history_root, runtime_root=runtime_root)
    assert result["updated_rows"] == 0
    updated = pd.read_csv(history_root / "pick_history.csv")
    assert updated.iloc[0]["result_status"] == "pending"


def test_over_under_grading_logic_works(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    history_root.mkdir(parents=True, exist_ok=True)
    date = "2026-04-25"
    pd.DataFrame(
        [
            {
                "prediction_date": date,
                "run_timestamp": "2026-04-25T12:00:00+00:00",
                "player_name": "Over Guy",
                "team": "BOS",
                "opponent": "NYK",
                "game_id": "",
                "market": "points",
                "selection": "over",
                "line": 20.5,
                "projection": 23.0,
                "edge": 2.5,
                "abs_edge": 2.5,
                "odds": "-110",
                "confidence": "0.7",
                "quality_score": 70.0,
                "qualification_reason": "pass",
                "provider_used": "test",
                "result_status": "pending",
            },
            {
                "prediction_date": date,
                "run_timestamp": "2026-04-25T12:00:00+00:00",
                "player_name": "Under Guy",
                "team": "MIA",
                "opponent": "LAL",
                "game_id": "",
                "market": "points",
                "selection": "under",
                "line": 12.5,
                "projection": 10.0,
                "edge": -2.5,
                "abs_edge": 2.5,
                "odds": "-110",
                "confidence": "0.7",
                "quality_score": 70.0,
                "qualification_reason": "pass",
                "provider_used": "test",
                "result_status": "pending",
            },
        ]
    ).to_csv(history_root / "pick_history.csv", index=False)

    actual_df = pd.DataFrame(
        [
            {
                "entity_name": "Over Guy",
                "selection": "over",
                "market_type": "points",
                "sportsbook_line": 20.5,
                "graded_result": "win",
            },
            {
                "entity_name": "Under Guy",
                "selection": "under",
                "market_type": "points",
                "sportsbook_line": 12.5,
                "graded_result": "loss",
            },
        ]
    )

    import scripts.history_tracking as history_tracking

    original_loader = history_tracking._load_actual_results_for_date
    history_tracking._load_actual_results_for_date = lambda *_args, **_kwargs: actual_df.copy()
    try:
        grade_completed_picks(history_root=history_root, runtime_root=runtime_root)
    finally:
        history_tracking._load_actual_results_for_date = original_loader
    updated = pd.read_csv(history_root / "pick_history.csv")
    assert sorted(updated["result_status"].tolist()) == ["hit", "miss"]


def test_performance_summary_updates_correctly(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    history_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "prediction_date": "2026-04-26",
                "run_timestamp": "2026-04-26T12:00:00+00:00",
                "player_name": "A",
                "team": "BOS",
                "opponent": "NYK",
                "game_id": "",
                "market": "points",
                "selection": "over",
                "line": 10.5,
                "projection": 12.0,
                "edge": 1.5,
                "abs_edge": 1.5,
                "odds": "-110",
                "confidence": "0.7",
                "quality_score": 70.0,
                "qualification_reason": "pass",
                "provider_used": "test",
                "result_status": "hit",
            },
            {
                "prediction_date": "2026-04-26",
                "run_timestamp": "2026-04-26T12:00:00+00:00",
                "player_name": "B",
                "team": "BOS",
                "opponent": "NYK",
                "game_id": "",
                "market": "points",
                "selection": "under",
                "line": 8.5,
                "projection": 7.0,
                "edge": -1.5,
                "abs_edge": 1.5,
                "odds": "-110",
                "confidence": "0.7",
                "quality_score": 70.0,
                "qualification_reason": "pass",
                "provider_used": "test",
                "result_status": "miss",
            },
        ]
    ).to_csv(history_root / "pick_history.csv", index=False)
    _write_audit(runtime_root / "operator" / "elite_pipeline_audit_summary_2026-04-26.json", max_team=2, max_game=4)

    update_performance_summaries(history_root=history_root, runtime_root=runtime_root)
    perf = pd.read_csv(history_root / "performance_summary.csv")
    assert len(perf) == 1
    assert perf.iloc[0]["total_picks"] == 2
    assert perf.iloc[0]["hits"] == 1
    assert perf.iloc[0]["misses"] == 1
    assert perf.iloc[0]["hit_rate"] == 0.5
    assert perf.iloc[0]["max_team_exposure"] == 2
    assert perf.iloc[0]["max_game_exposure"] == 4


def test_dashboard_loader_handles_empty_files(tmp_path: Path) -> None:
    history_root = tmp_path / "data" / "history"
    history_root.mkdir(parents=True, exist_ok=True)
    data = load_dashboard_data(history_root=history_root)
    assert set(data.keys()) == {"pick_history", "performance_summary", "by_side", "by_edge", "by_qualification"}
    for value in data.values():
        assert isinstance(value, pd.DataFrame)


# ---------------------------------------------------------------------------
# Patch 2 tests: schema migration, Kelly context, cross-slate, grading warning
# ---------------------------------------------------------------------------


def test_pick_history_migration_adds_new_columns_preserves_rows(tmp_path: Path) -> None:
    """migrate_pick_history_schema adds missing columns without destroying rows."""
    history_root = tmp_path / "data" / "history"
    history_root.mkdir(parents=True, exist_ok=True)

    # Write a pick_history with only the original (pre-Patch-2) columns
    old_cols = ["prediction_date", "run_timestamp", "player_name", "team", "opponent",
                "game_id", "market", "selection", "line", "projection", "edge", "abs_edge",
                "odds", "confidence", "quality_score", "qualification_reason",
                "provider_used", "result_status"]
    pd.DataFrame([{
        "prediction_date": "2026-04-01",
        "run_timestamp": "2026-04-01T12:00:00+00:00",
        "player_name": "Alice",
        "team": "BOS",
        "opponent": "NYK",
        "game_id": "g1",
        "market": "player_points",
        "selection": "over",
        "line": 20.5,
        "projection": 22.0,
        "edge": 1.5,
        "abs_edge": 1.5,
        "odds": "-110",
        "confidence": "0.75",
        "quality_score": 80.0,
        "qualification_reason": "pass",
        "provider_used": "test",
        "result_status": "hit",
    }]).to_csv(history_root / "pick_history.csv", index=False)

    result = migrate_pick_history_schema(history_root=history_root)

    assert result["status"] == "ok"
    for col in ("kelly_eligible", "skip_reason", "context_caution_level", "context_pick_alignment", "line_source"):
        assert col in result["added_columns"], f"{col} should have been added"

    updated = pd.read_csv(history_root / "pick_history.csv")
    assert len(updated) == 1, "existing row must be preserved"
    assert updated.iloc[0]["player_name"] == "Alice"
    assert updated.iloc[0]["result_status"] == "hit"
    for col in PICK_HISTORY_COLUMNS:
        assert col in updated.columns, f"column {col} missing after migration"

    # Idempotent: second run reports no changes
    result2 = migrate_pick_history_schema(history_root=history_root)
    assert result2["added_columns"] == []
    assert len(pd.read_csv(history_root / "pick_history.csv")) == 1


def test_persist_daily_picks_carries_kelly_context_metadata(tmp_path: Path) -> None:
    """persist_daily_picks populates kelly/context columns from kelly_stakes CSV."""
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    date = "2026-05-01"

    _write_elite_board(
        runtime_root / "operator" / f"elite_board_{date}.csv",
        [{
            "prediction_date": date,
            "player_name": "Context Player",
            "market_type": "player_points",
            "selection": "over",
            "sportsbook_line": 22.5,
            "model_projection": 24.0,
            "edge": 1.5,
            "odds": -110,
            "confidence": 0.75,
            "quality_score": 82.0,
            "qualification_reason": "pass",
            "context_caution_level": "high",
            "context_pick_alignment": "conflicted",
            "line_source": "live_market",
        }],
    )
    _write_audit(runtime_root / "operator" / f"elite_pipeline_audit_summary_{date}.json")

    # Write kelly_stakes with eligibility metadata
    kelly_path = runtime_root / "operator" / f"kelly_stakes_{date}.csv"
    kelly_path.parent.mkdir(parents=True, exist_ok=True)
    with kelly_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "prediction_date", "player_name", "market_type", "selection", "line",
            "kelly_eligible", "eligible", "skip_reason", "context_caution_level", "context_pick_alignment",
        ])
        writer.writeheader()
        writer.writerow({
            "prediction_date": date,
            "player_name": "Context Player",
            "market_type": "player_points",
            "selection": "over",
            "line": "22.5",
            "kelly_eligible": "True",
            "eligible": "True",
            "skip_reason": "",
            "context_caution_level": "high",
            "context_pick_alignment": "conflicted",
        })

    result = persist_daily_picks(prediction_date=date, runtime_root=runtime_root, history_root=history_root)
    assert result["appended_rows"] == 1

    history = pd.read_csv(history_root / "pick_history.csv")
    row = history.iloc[0]
    # pandas reads "True" from CSV as bool True — normalise before comparing
    assert str(row["kelly_eligible"]).strip() == "True"
    assert str(row.get("skip_reason", "")).strip() in ("", "nan")
    assert row["context_caution_level"] == "high"
    assert row["context_pick_alignment"] == "conflicted"
    assert row["line_source"] == "live_market"


def test_cross_slate_aggregation_output_written(tmp_path: Path) -> None:
    """update_performance_summaries writes performance_context_cross_slate.csv with expected groups."""
    history_root = tmp_path / "data" / "history"
    history_root.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "outputs" / "runtime"

    pd.DataFrame([
        {
            "prediction_date": "2026-04-01",
            "run_timestamp": "2026-04-01T12:00:00+00:00",
            "player_name": "A",
            "team": "BOS", "opponent": "NYK", "game_id": "",
            "market": "player_points", "selection": "over",
            "line": 20.5, "projection": 22.0, "edge": 1.5, "abs_edge": 1.5,
            "odds": "-110", "confidence": "0.75", "quality_score": 80.0,
            "qualification_reason": "pass", "provider_used": "test",
            "result_status": "hit",
            "kelly_eligible": "True", "skip_reason": "",
            "context_caution_level": "low", "context_pick_alignment": "aligned",
            "line_source": "live_market",
        },
        {
            "prediction_date": "2026-04-02",
            "run_timestamp": "2026-04-02T12:00:00+00:00",
            "player_name": "B",
            "team": "MIA", "opponent": "LAL", "game_id": "",
            "market": "player_rebounds", "selection": "over",
            "line": 5.5, "projection": 4.0, "edge": -1.5, "abs_edge": 1.5,
            "odds": "-110", "confidence": "0.65", "quality_score": 75.0,
            "qualification_reason": "pass", "provider_used": "test",
            "result_status": "miss",
            "kelly_eligible": "False", "skip_reason": "context_high_caution_over",
            "context_caution_level": "high", "context_pick_alignment": "conflicted",
            "line_source": "live_market",
        },
    ]).to_csv(history_root / "pick_history.csv", index=False)

    update_performance_summaries(history_root=history_root, runtime_root=runtime_root)

    cross_path = history_root / "performance_context_cross_slate.csv"
    assert cross_path.exists(), "performance_context_cross_slate.csv must be written"

    cross = pd.read_csv(cross_path)
    dims = set(cross["dimension"].unique())
    for expected_dim in ("kelly_eligible", "skip_reason", "context_caution_level", "context_pick_alignment", "market", "selection"):
        assert expected_dim in dims, f"dimension '{expected_dim}' missing from cross-slate output"

    high_caution = cross[
        (cross["dimension"] == "context_caution_level") & (cross["group_value"] == "high")
    ]
    assert len(high_caution) == 1
    assert high_caution.iloc[0]["total"] == 1
    assert high_caution.iloc[0]["misses"] == 1
    assert high_caution.iloc[0]["hit_rate"] == 0.0

    eligible_true = cross[
        (cross["dimension"] == "kelly_eligible") & (cross["group_value"] == "True")
    ]
    assert len(eligible_true) == 1
    assert eligible_true.iloc[0]["hits"] == 1


def test_cross_slate_pending_only_and_milestone_segments_are_not_calibration(tmp_path: Path) -> None:
    """Zero-graded and unsupported-selection groups stay visible without fake hit rates."""
    history_root = tmp_path / "data" / "history"
    history_root.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "outputs" / "runtime"

    rows = [
        _history_row(
            "Pending Kelly",
            result_status="pending",
            kelly_eligible="True",
            context_caution_level="low",
            context_pick_alignment="aligned",
        ),
        _history_row("Legacy Hit", result_status="hit"),
        _history_row("Legacy Miss", result_status="miss"),
        _history_row("Milestone Pending", selection="milestone", result_status="pending"),
    ]
    pd.DataFrame(rows).to_csv(history_root / "pick_history.csv", index=False)

    update_performance_summaries(history_root=history_root, runtime_root=runtime_root)
    cross = pd.read_csv(history_root / "performance_context_cross_slate.csv")

    expected_columns = {
        "dimension",
        "group_value",
        "total",
        "hits",
        "misses",
        "pushes",
        "pending",
        "graded_total",
        "hit_rate",
        "sample_status",
        "calibration_eligible",
        "calibration_exclusion_reason",
    }
    assert expected_columns.issubset(cross.columns)

    pending_kelly = _cross_row(cross, "kelly_eligible", "True")
    assert pending_kelly["graded_total"] == 0
    assert pd.isna(pending_kelly["hit_rate"])
    assert pending_kelly["sample_status"] == "no_graded_results"
    assert not _bool_value(pending_kelly["calibration_eligible"])
    assert "no_graded_results" in pending_kelly["calibration_exclusion_reason"]

    milestone = _cross_row(cross, "selection", "milestone")
    assert milestone["graded_total"] == 0
    assert pd.isna(milestone["hit_rate"])
    assert milestone["sample_status"] == "no_graded_results"
    assert not _bool_value(milestone["calibration_eligible"])
    assert "unsupported_selection_milestone" in milestone["calibration_exclusion_reason"]

    legacy_blank = _cross_row(cross, "kelly_eligible", "(blank)")
    assert legacy_blank["graded_total"] == 2
    assert legacy_blank["sample_status"] == "insufficient_sample"
    assert not _bool_value(legacy_blank["calibration_eligible"])
    assert "legacy_missing_metadata" in legacy_blank["calibration_exclusion_reason"]


def test_cross_slate_sample_status_and_graded_hit_rate(tmp_path: Path) -> None:
    """Over/under hit rates use hits/(hits+misses) while sample buckets are explicit."""
    history_root = tmp_path / "data" / "history"
    history_root.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "outputs" / "runtime"

    rows = []
    for idx in range(12):
        rows.append(_history_row(f"Over Hit {idx}", selection="over", result_status="hit"))
    for idx in range(8):
        rows.append(_history_row(f"Over Miss {idx}", selection="over", result_status="miss"))
    for idx in range(3):
        rows.append(_history_row(f"Under Hit {idx}", selection="under", result_status="hit"))
    for idx in range(2):
        rows.append(_history_row(f"Under Miss {idx}", selection="under", result_status="miss"))
    pd.DataFrame(rows).to_csv(history_root / "pick_history.csv", index=False)

    update_performance_summaries(history_root=history_root, runtime_root=runtime_root)
    cross = pd.read_csv(history_root / "performance_context_cross_slate.csv")

    over = _cross_row(cross, "selection", "over")
    assert over["graded_total"] == 20
    assert over["hit_rate"] == 0.6
    assert over["sample_status"] == "usable_sample"
    assert _bool_value(over["calibration_eligible"])
    assert str(over.get("calibration_exclusion_reason", "")).strip() in ("", "nan")

    under = _cross_row(cross, "selection", "under")
    assert under["graded_total"] == 5
    assert under["hit_rate"] == 0.6
    assert under["sample_status"] == "insufficient_sample"
    assert not _bool_value(under["calibration_eligible"])
    assert "insufficient_sample" in under["calibration_exclusion_reason"]


def test_grading_empty_result_warning_is_emitted(tmp_path: Path, capsys) -> None:
    """_load_actual_results_for_date prints a warning when auto_grade returns empty."""
    import scripts.history_tracking as ht

    with patch.object(ht, "CourtVisionAI") as mock_ai_cls:
        mock_ai_cls.return_value.auto_grade.return_value = pd.DataFrame()
        ht._load_actual_results_for_date("2026-04-01", tmp_path)

    captured = capsys.readouterr()
    assert "[history_tracking] WARNING" in captured.out
    assert "auto_grade returned no results" in captured.out
    assert "2026-04-01" in captured.out


def test_candidate_scoring_py_is_untouched() -> None:
    """Confirm candidate_scoring.py was not modified by this patch."""
    path = Path(__file__).parent.parent / "courtvision" / "scoring" / "candidate_scoring.py"
    assert path.exists(), "candidate_scoring.py must exist"
    content = path.read_text(encoding="utf-8")
    # None of the symbols introduced by Patch 2 should appear in candidate_scoring
    assert "migrate_pick_history_schema" not in content
    assert "performance_context_cross_slate" not in content
    assert "calibration_eligible" not in content
    assert "_build_kelly_lookup" not in content

