from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.quality_summary import write_quality_summary_outputs
from scripts.history_tracking import MARKET_SHADOW_HISTORY_COLUMNS, PICK_HISTORY_COLUMNS


def test_history_columns_include_fragility_fields() -> None:
    for col in [
        "fragility_score", "fragility_bucket", "fragility_reasons",
        "survivability_score", "survivability_bucket", "survivability_reasons",
    ]:
        assert col in MARKET_SHADOW_HISTORY_COLUMNS
        assert col in PICK_HISTORY_COLUMNS


def test_fragility_attribution_report_generation(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    history = tmp_path / "outputs" / "history"
    operator.mkdir(parents=True, exist_ok=True)
    diagnostics.mkdir(parents=True, exist_ok=True)
    history.mkdir(parents=True, exist_ok=True)

    date = "2026-05-04"
    pd.DataFrame([{"prediction_date": date}]).to_csv(operator / f"elite_board_{date}.csv", index=False)
    pd.DataFrame([{"prediction_date": date}]).to_csv(operator / f"full_market_board_{date}.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{date}.csv", index=False)
    (runtime_root / "research").mkdir(parents=True, exist_ok=True)
    (runtime_root / "research" / f"player_predictions_{date}.csv").write_text("", encoding="utf-8")
    (runtime_root / "research" / f"model_metrics_{date}.json").write_text("{}", encoding="utf-8")
    (operator / f"elite_pipeline_audit_summary_{date}.json").write_text("{}", encoding="utf-8")
    (diagnostics / f"board_diagnostics_{date}.json").write_text("{}", encoding="utf-8")

    pd.DataFrame([
        {"prediction_date": date, "result_status": "hit", "fragility_bucket": "HIGH", "survivability_bucket": "LOW", "edge": 1.0, "confidence": 0.6, "quality_score": 70},
        {"prediction_date": date, "result_status": "miss", "fragility_bucket": "LOW", "survivability_bucket": "HIGH", "edge": -0.2, "confidence": 0.5, "quality_score": 65},
    ]).to_csv(history / "market_shadow_history.csv", index=False)

    _txt, _json, payload = write_quality_summary_outputs(prediction_date=date, runtime_root=runtime_root, out_dir=tmp_path / "outputs")
    attr = payload.get("fragility_survivability_attribution", {})
    assert attr.get("status") == "insufficient_sample"
    report_path = Path(attr.get("path", ""))
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "by_fragility_bucket" in report
    assert "by_survivability_bucket" in report
    trends_meta = payload.get("fragility_survivability_trends", {})
    trends_path = Path(trends_meta.get("path", ""))
    assert trends_path.exists()
    trends = json.loads(trends_path.read_text(encoding="utf-8"))
    assert any(window.get("window") == "7d" for window in trends.get("windows", []))
    assert any(window.get("window") == "all_available" for window in trends.get("windows", []))
