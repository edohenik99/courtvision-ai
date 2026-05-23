from __future__ import annotations

from pathlib import Path

import pytest

from scripts import post_run_tracking


def test_grade_pending_is_scoped_to_prediction_date(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, dict[str, object]] = {}

    def fake_persist_daily_picks(**kwargs: object) -> dict[str, object]:
        return {"appended_rows": 0, "total_rows": 0, "dry_run": bool(kwargs.get("dry_run"))}

    def fake_persist_market_shadow_history(**kwargs: object) -> dict[str, object]:
        return {
            "current_date_rows": 0,
            "current_date_non_points_rows": 0,
            "market_shadow_history_path": tmp_path / "history" / "market_shadow_history.csv",
            "market_readiness_summary_path": tmp_path / "history" / "market_readiness_summary.csv",
            "dry_run": bool(kwargs.get("dry_run")),
        }

    def fake_grade_completed_picks(**kwargs: object) -> dict[str, object]:
        captured["grade"] = dict(kwargs)
        return {"updated_rows": 0, "dry_run": bool(kwargs.get("dry_run"))}

    monkeypatch.setattr(post_run_tracking, "persist_daily_picks", fake_persist_daily_picks)
    monkeypatch.setattr(post_run_tracking, "persist_market_shadow_history", fake_persist_market_shadow_history)
    monkeypatch.setattr(post_run_tracking, "grade_completed_picks", fake_grade_completed_picks)

    rc = post_run_tracking.main(
        [
            "--prediction-date",
            "2026-05-06",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--history-root",
            str(tmp_path / "history"),
            "--grade-pending",
            "--dry-run",
        ]
    )

    assert rc == 0
    assert captured["grade"]["prediction_date"] == "2026-05-06"
    assert captured["grade"]["dry_run"] is True
