from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd

from scripts import write_daily_summary as daily_summary


def test_write_daily_summary_no_slate_safe_skips_auxiliary_writers(monkeypatch, tmp_path: Path) -> None:
    prediction_date = "2026-05-16"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    def fail_if_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("auxiliary writer should not be called on no-slate-safe path")

    monkeypatch.setattr(daily_summary, "annotate_operator_board_files", fail_if_called)
    monkeypatch.setattr(daily_summary, "persist_market_shadow_history", fail_if_called)
    monkeypatch.setattr(daily_summary, "write_high_caution_over_watchlist", fail_if_called)
    monkeypatch.setattr(daily_summary, "write_combo_under_watchlist", fail_if_called)
    monkeypatch.setattr(daily_summary, "write_promotion_readiness_report", fail_if_called)
    monkeypatch.setattr(daily_summary, "write_paper_kelly_simulation", fail_if_called)
    monkeypatch.setattr(daily_summary, "write_paper_kelly_performance_report", fail_if_called)
    monkeypatch.setattr(daily_summary, "write_correlation_exposure_report", fail_if_called)
    monkeypatch.setattr(daily_summary, "write_team_distribution_report", fail_if_called)

    output_path, metadata = daily_summary.write_daily_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        write_board_annotations=False,
        persist_shadow_history=False,
        skip_auxiliary_on_no_slate=True,
    )

    assert output_path == runtime_root / "operator" / f"daily_summary_{prediction_date}.txt"
    assert output_path.exists()
    assert "Run Health: NO_SLATE" in output_path.read_text(encoding="utf-8")
    assert metadata["run_health_status"] == "NO_SLATE"
    assert metadata["no_slate_safe"] is True
    assert metadata["auxiliary_reports_skipped"] is True
    assert metadata["elite_count"] == 0
    assert metadata["full_market_count"] == 0
    assert metadata["market_shadow_rows"] == 0
    assert metadata["kelly_review_before_bet_count"] == 0
    assert metadata["total_exposure"] == 0.0
    assert metadata["expected_ev"] == 0.0
    assert metadata["pending_grading_count"] == 0

    written_files = list((runtime_root / "operator").glob(f"*{prediction_date}*"))
    assert written_files == [output_path]


def test_write_daily_summary_no_slate_safe_does_not_trigger_when_primary_board_exists(
    monkeypatch,
    tmp_path: Path,
) -> None:
    prediction_date = "2026-05-16"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    full_market_path = runtime_root / "operator" / f"full_market_board_{prediction_date}.csv"
    full_market_path.parent.mkdir(parents=True, exist_ok=True)
    full_market_path.write_text("prediction_date,player_name\n2026-05-16,Test Player\n", encoding="utf-8")

    called = {"build_daily_summary": False}

    def fake_build_daily_summary(**kwargs):  # noqa: ANN003
        called["build_daily_summary"] = True
        return "normal summary\n", {
            "elite_count": 0,
            "kelly_eligible_count": 0,
            "run_health_status": "TEST",
        }

    monkeypatch.setattr(
        daily_summary,
        "annotate_operator_board_files",
        lambda **kwargs: {"prediction_date": prediction_date, "boards": {}},
    )
    monkeypatch.setattr(
        daily_summary,
        "persist_market_shadow_history",
        lambda **kwargs: {
            "current_date_rows": 0,
            "current_date_non_points_rows": 0,
            "market_shadow_history_path": history_root / "market_shadow_history.csv",
            "market_readiness_summary_path": history_root / "market_readiness_summary.csv",
        },
    )
    monkeypatch.setattr(
        daily_summary,
        "write_high_caution_over_watchlist",
        lambda **kwargs: (runtime_root / "operator" / "high.csv", pd.DataFrame()),
    )
    monkeypatch.setattr(
        daily_summary,
        "write_combo_under_watchlist",
        lambda **kwargs: (runtime_root / "operator" / "combo.csv", pd.DataFrame()),
    )
    monkeypatch.setattr(
        daily_summary,
        "write_promotion_readiness_report",
        lambda **kwargs: (runtime_root / "operator" / "promo.txt", runtime_root / "operator" / "promo.csv", pd.DataFrame()),
    )
    monkeypatch.setattr(
        daily_summary,
        "write_paper_kelly_simulation",
        lambda **kwargs: (runtime_root / "operator" / "paper.txt", runtime_root / "operator" / "paper.csv", pd.DataFrame()),
    )
    monkeypatch.setattr(
        daily_summary,
        "write_paper_kelly_performance_report",
        lambda **kwargs: (
            runtime_root / "operator" / "perf.txt",
            runtime_root / "operator" / "perf.csv",
            pd.DataFrame(),
            {"paper_kelly_history_path": history_root / "paper.csv", "current_date_rows": 0, "pending_rows": 0},
        ),
    )
    monkeypatch.setattr(
        daily_summary,
        "write_correlation_exposure_report",
        lambda **kwargs: (runtime_root / "operator" / "corr.txt", runtime_root / "operator" / "corr.csv", pd.DataFrame(), {}),
    )
    monkeypatch.setattr(
        daily_summary,
        "write_team_distribution_report",
        lambda **kwargs: (runtime_root / "operator" / "team.txt", runtime_root / "operator" / "team.csv", pd.DataFrame(), {}),
    )
    monkeypatch.setattr(daily_summary, "build_daily_summary", fake_build_daily_summary)

    _output_path, metadata = daily_summary.write_daily_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        skip_auxiliary_on_no_slate=True,
    )

    assert called["build_daily_summary"] is True
    assert metadata["run_health_status"] == "TEST"


def test_write_daily_summary_main_closed_slate_safe_enables_no_slate_safe(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_write_daily_summary_outputs(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        metadata = defaultdict(int)
        metadata["run_health_status"] = "NO_SLATE"
        return tmp_path / "daily_summary_2026-05-16.txt", metadata

    monkeypatch.setattr(daily_summary, "write_daily_summary_outputs", fake_write_daily_summary_outputs)

    rc = daily_summary.main(["--prediction-date", "2026-05-16", "--closed-slate-safe"])

    assert rc == 0
    assert captured["write_board_annotations"] is False
    assert captured["persist_shadow_history"] is False
    assert captured["skip_auxiliary_on_no_slate"] is True


def test_write_daily_summary_main_explicit_no_slate_safe(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_write_daily_summary_outputs(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        metadata = defaultdict(int)
        metadata["run_health_status"] = "NO_SLATE"
        return tmp_path / "daily_summary_2026-05-16.txt", metadata

    monkeypatch.setattr(daily_summary, "write_daily_summary_outputs", fake_write_daily_summary_outputs)

    rc = daily_summary.main(["--prediction-date", "2026-05-16", "--no-slate-safe"])

    assert rc == 0
    assert captured["skip_auxiliary_on_no_slate"] is True
