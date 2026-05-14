from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.completion_state_audit import (
    STATUS_COMPLETE,
    STATUS_COMPLETE_WITH_SHADOW_OPEN_NOISE,
    STATUS_INCONSISTENT_REPORTING,
    STATUS_STALE_PENDING_RISK,
    build_completion_state_audit,
    write_completion_state_audit,
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_daily_summary(runtime_root: Path, prediction_date: str, pending: int) -> None:
    path = runtime_root / "operator" / f"daily_summary_{prediction_date}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"Daily Summary - {prediction_date}\nPending grading count: {pending}\n", encoding="utf-8")


def _write_quality_summary(runtime_root: Path, prediction_date: str, *, elite: int, kelly: int, pending: int = 0) -> None:
    _write_json(
        runtime_root / "operator" / f"quality_summary_{prediction_date}.json",
        {
            "candidate_funnel": {
                "elite_board_count": elite,
                "kelly_rows_count": kelly,
            },
            "kelly_decision_performance": {
                "overall": {
                    "pending_count": pending,
                }
            },
        },
    )


def _pick_row(
    player_name: str,
    *,
    prediction_date: str,
    result_status: str = "hit",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "market": "player_points",
        "selection": "under",
        "line": 18.5,
        "result_status": result_status,
        "actual_value": 13.0 if result_status != "pending" else "",
    }


def _history_row(
    player_name: str,
    *,
    prediction_date: str,
    result_status: str = "pending",
    grading_skip_reason: str = "",
    game_status: str = "",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "market_type": "player_points",
        "selection": "under",
        "line": 18.5,
        "result_status": result_status,
        "actual_value": 13.0 if result_status in {"hit", "miss", "push"} else "",
        "grading_skip_reason": grading_skip_reason,
        "game_status": game_status,
    }


def test_fully_complete_real_pick_date(tmp_path: Path) -> None:
    prediction_date = "2026-01-10"
    history_root = tmp_path / "data" / "history"
    runtime_root = tmp_path / "outputs" / "runtime"
    _write_csv(
        history_root / "pick_history.csv",
        [
            _pick_row("Hit One", prediction_date=prediction_date, result_status="hit"),
            _pick_row("Miss One", prediction_date=prediction_date, result_status="miss"),
            _pick_row("Push One", prediction_date=prediction_date, result_status="push"),
        ],
    )
    _write_csv(history_root / "market_shadow_history.csv", [])
    _write_csv(history_root / "paper_kelly_history.csv", [])
    _write_daily_summary(runtime_root, prediction_date, pending=0)
    _write_quality_summary(runtime_root, prediction_date, elite=3, kelly=3)

    payload = build_completion_state_audit(
        prediction_date=prediction_date,
        history_root=history_root,
        runtime_root=runtime_root,
    )

    assert payload["report_agreement_status"] == STATUS_COMPLETE
    assert payload["real_pick_rows"] == 3
    assert payload["real_pick_graded_count"] == 3
    assert payload["real_pick_hit_count"] == 1
    assert payload["real_pick_miss_count"] == 1
    assert payload["real_pick_push_count"] == 1


def test_complete_real_picks_with_shadow_open_game_pending_noise(tmp_path: Path) -> None:
    prediction_date = "2026-01-15"
    history_root = tmp_path / "data" / "history"
    runtime_root = tmp_path / "outputs" / "runtime"
    _write_csv(history_root / "pick_history.csv", [_pick_row("Real Pick", prediction_date=prediction_date)])
    _write_csv(
        history_root / "market_shadow_history.csv",
        [
            _history_row("Shadow One", prediction_date=prediction_date),
            _history_row("Shadow Two", prediction_date=prediction_date),
        ],
    )
    _write_csv(
        history_root / "paper_kelly_history.csv",
        [_history_row("Paper One", prediction_date=prediction_date, grading_skip_reason="market_shadow_history_result_pending")],
    )
    _write_json(
        runtime_root / "diagnostics" / f"pending_repair_audit_{prediction_date}.json",
        {
            "mode": "all_completed",
            "start_date": prediction_date,
            "end_date": prediction_date,
            "include_current_date": True,
            "histories": {
                "market_shadow_history": {"total_pending": 2, "open_game_pending": 2, "stale_pending": 0},
                "paper_kelly_history": {"total_pending": 1, "open_game_pending": 1, "stale_pending": 0},
            },
        },
    )
    _write_daily_summary(runtime_root, prediction_date, pending=0)
    _write_quality_summary(runtime_root, prediction_date, elite=1, kelly=1)

    payload = build_completion_state_audit(
        prediction_date=prediction_date,
        history_root=history_root,
        runtime_root=runtime_root,
    )

    assert payload["report_agreement_status"] == STATUS_COMPLETE_WITH_SHADOW_OPEN_NOISE
    assert payload["shadow_pending_count"] == 2
    assert payload["shadow_open_game_pending_count"] == 2
    assert payload["shadow_stale_pending_count"] == 0
    assert payload["paper_open_game_pending_count"] == 1
    assert payload["details"]["shadow_pending_taxonomy_source"] == "pending_repair_audit"


def test_stale_pending_risk(tmp_path: Path) -> None:
    prediction_date = "2026-01-20"
    history_root = tmp_path / "data" / "history"
    runtime_root = tmp_path / "outputs" / "runtime"
    _write_csv(history_root / "pick_history.csv", [_pick_row("Real Pick", prediction_date=prediction_date)])
    _write_csv(history_root / "market_shadow_history.csv", [_history_row("Stale Shadow", prediction_date=prediction_date)])
    _write_csv(history_root / "paper_kelly_history.csv", [])
    _write_daily_summary(runtime_root, prediction_date, pending=0)
    _write_quality_summary(runtime_root, prediction_date, elite=1, kelly=1)

    payload = build_completion_state_audit(
        prediction_date=prediction_date,
        history_root=history_root,
        runtime_root=runtime_root,
    )

    assert payload["report_agreement_status"] == STATUS_STALE_PENDING_RISK
    assert payload["shadow_stale_pending_count"] == 1
    assert payload["shadow_open_game_pending_count"] == 0


def test_inconsistent_daily_summary_pending_count(tmp_path: Path) -> None:
    prediction_date = "2026-01-25"
    history_root = tmp_path / "data" / "history"
    runtime_root = tmp_path / "outputs" / "runtime"
    _write_csv(history_root / "pick_history.csv", [_pick_row("Real Pick", prediction_date=prediction_date)])
    _write_csv(history_root / "market_shadow_history.csv", [])
    _write_csv(history_root / "paper_kelly_history.csv", [])
    _write_daily_summary(runtime_root, prediction_date, pending=1)
    _write_quality_summary(runtime_root, prediction_date, elite=1, kelly=1)

    payload = build_completion_state_audit(
        prediction_date=prediction_date,
        history_root=history_root,
        runtime_root=runtime_root,
    )

    assert payload["report_agreement_status"] == STATUS_INCONSISTENT_REPORTING
    assert any("daily_summary_pending_grading_mismatch" in issue for issue in payload["agreement_issues"])


def test_missing_optional_files_do_not_crash(tmp_path: Path) -> None:
    prediction_date = "2026-01-30"
    text_path, json_path, payload = write_completion_state_audit(
        prediction_date=prediction_date,
        history_root=tmp_path / "missing_history",
        runtime_root=tmp_path / "runtime",
    )

    assert text_path.exists()
    assert json_path.exists()
    assert payload["report_agreement_status"] == STATUS_COMPLETE
    assert payload["real_pick_rows"] == 0
    assert payload["warnings"]
