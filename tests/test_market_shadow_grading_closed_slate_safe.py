from __future__ import annotations

import json
from pathlib import Path

from scripts import market_shadow_grading as shadow


def _payload() -> dict:
    return {
        "prediction_date": "2026-05-16",
        "source": "test",
        "scope": "full_market_shadow_grading",
        "elite_locked_to": ["player_points"],
        "kelly_locked_to": ["player_points"],
        "totals": {
            "total_picks": 0,
            "graded_picks": 0,
            "pending_picks": 0,
        },
        "markets": [],
        "context_alignment_performance": {"by_alignment": {}},
        "kelly_decision_performance": {"test_metric": 123},
    }


def test_write_market_shadow_outputs_can_skip_grading_summary_update(monkeypatch, tmp_path: Path) -> None:
    prediction_date = "2026-05-16"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    grading_summary_path = runtime_root / "diagnostics" / f"grading_summary_{prediction_date}.json"
    grading_summary_path.parent.mkdir(parents=True, exist_ok=True)
    original = {
        "existing": True,
        "kelly_decision_performance": {"old_metric": 1},
    }
    grading_summary_path.write_text(json.dumps(original, indent=2), encoding="utf-8")

    monkeypatch.setattr(shadow, "build_market_shadow_grading", lambda **kwargs: _payload())

    diagnostics_path, report_path, payload = shadow.write_market_shadow_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        update_grading_summary=False,
    )

    assert diagnostics_path.exists()
    assert report_path.exists()
    assert json.loads(grading_summary_path.read_text(encoding="utf-8")) == original
    assert payload["grading_summary_update_enabled"] is False
    assert payload["grading_summary_update_skipped"] is True


def test_write_market_shadow_outputs_default_updates_grading_summary(monkeypatch, tmp_path: Path) -> None:
    prediction_date = "2026-05-16"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    grading_summary_path = runtime_root / "diagnostics" / f"grading_summary_{prediction_date}.json"
    grading_summary_path.parent.mkdir(parents=True, exist_ok=True)
    grading_summary_path.write_text(json.dumps({"existing": True}, indent=2), encoding="utf-8")

    monkeypatch.setattr(shadow, "build_market_shadow_grading", lambda **kwargs: _payload())

    diagnostics_path, report_path, payload = shadow.write_market_shadow_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    updated = json.loads(grading_summary_path.read_text(encoding="utf-8"))
    assert diagnostics_path.exists()
    assert report_path.exists()
    assert updated["kelly_decision_performance"] == {"test_metric": 123}
    assert payload["grading_summary_update_enabled"] is True
    assert payload["grading_summary_update_skipped"] is False


def test_market_shadow_main_closed_slate_safe_passes_skip_flag(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_write_market_shadow_outputs(**kwargs):
        captured.update(kwargs)
        return (
            tmp_path / "market_shadow_grading_2026-05-16.json",
            tmp_path / "market_shadow_report_2026-05-16.txt",
            _payload(),
        )

    monkeypatch.setattr(shadow, "write_market_shadow_outputs", fake_write_market_shadow_outputs)

    shadow.main(["--prediction-date", "2026-05-16", "--closed-slate-safe"])

    assert captured["update_grading_summary"] is False


def test_market_shadow_main_explicit_skip_grading_summary_update(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_write_market_shadow_outputs(**kwargs):
        captured.update(kwargs)
        return (
            tmp_path / "market_shadow_grading_2026-05-16.json",
            tmp_path / "market_shadow_report_2026-05-16.txt",
            _payload(),
        )

    monkeypatch.setattr(shadow, "write_market_shadow_outputs", fake_write_market_shadow_outputs)

    shadow.main(["--prediction-date", "2026-05-16", "--skip-grading-summary-update"])

    assert captured["update_grading_summary"] is False
