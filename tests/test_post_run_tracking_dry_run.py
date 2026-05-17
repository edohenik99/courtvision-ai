from __future__ import annotations

from pathlib import Path

import pytest

from scripts import post_run_tracking


def test_post_run_tracking_dry_run_passes_to_persist_and_skips_performance_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, dict[str, object]] = {}

    def fake_persist_daily_picks(**kwargs: object) -> dict[str, object]:
        captured["daily"] = dict(kwargs)
        return {"appended_rows": 2, "total_rows": 5, "dry_run": bool(kwargs.get("dry_run"))}

    def fake_persist_market_shadow_history(**kwargs: object) -> dict[str, object]:
        captured["shadow"] = dict(kwargs)
        return {
            "current_date_rows": 4,
            "current_date_non_points_rows": 3,
            "market_shadow_history_path": tmp_path / "history" / "market_shadow_history.csv",
            "market_readiness_summary_path": tmp_path / "history" / "market_readiness_summary.csv",
            "dry_run": bool(kwargs.get("dry_run")),
        }

    def fail_update_performance_summaries(**_kwargs: object) -> None:
        raise AssertionError("update_performance_summaries should not run during dry-run")

    monkeypatch.setattr(post_run_tracking, "persist_daily_picks", fake_persist_daily_picks)
    monkeypatch.setattr(post_run_tracking, "persist_market_shadow_history", fake_persist_market_shadow_history)
    monkeypatch.setattr(post_run_tracking, "update_performance_summaries", fail_update_performance_summaries)

    rc = post_run_tracking.main(
        [
            "--prediction-date",
            "2026-05-06",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--history-root",
            str(tmp_path / "history"),
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert rc == 0
    assert captured["daily"]["dry_run"] is True
    assert captured["shadow"]["dry_run"] is True
    assert "dry_run=true" in output
    assert "performance_summary_update_skipped=true" in output


def test_post_run_tracking_dry_run_passes_to_grade_completed_picks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
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
        return {"updated_rows": 7, "dry_run": bool(kwargs.get("dry_run"))}

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

    output = capsys.readouterr().out
    assert rc == 0
    assert captured["grade"]["dry_run"] is True
    assert "graded_updates=7" in output
    assert "dry_run=true" in output
