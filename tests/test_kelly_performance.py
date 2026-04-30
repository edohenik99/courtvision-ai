from __future__ import annotations

import pandas as pd

from courtvision.reporting.kelly_performance import build_kelly_decision_performance, kelly_perf_log_lines
from scripts.market_shadow_grading import write_market_shadow_outputs


def test_kelly_decision_performance_tracks_eligible_and_high_caution_skips(tmp_path) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "operator").mkdir(parents=True)
    (runtime / "history").mkdir(parents=True)
    date = "2026-04-29"

    pd.DataFrame(
        [
            {
                "prediction_date": date,
                "player_name": "Eligible Winner",
                "market_type": "player_points",
                "selection": "under",
                "line": 20.5,
                "american_odds": -110,
                "edge_pct": 0.10,
                "side_edge_pct": 0.10,
                "confidence": 0.80,
                "eligible": True,
                "skip_reason": "",
                "context_caution_level": "high",
                "context_pick_alignment": "aligned",
            },
            {
                "prediction_date": date,
                "player_name": "Skipped Winner",
                "market_type": "player_points",
                "selection": "over",
                "line": 15.5,
                "american_odds": -120,
                "edge_pct": 0.20,
                "side_edge_pct": 0.20,
                "confidence": 0.75,
                "eligible": False,
                "skip_reason": "context_high_caution_over",
                "context_caution_level": "high",
                "context_pick_alignment": "conflicted",
            },
            {
                "prediction_date": date,
                "player_name": "Pending Skip",
                "market_type": "player_points",
                "selection": "over",
                "line": 10.5,
                "american_odds": -115,
                "edge_pct": 0.12,
                "side_edge_pct": 0.12,
                "confidence": 0.70,
                "eligible": False,
                "skip_reason": "context_high_caution_over",
                "context_caution_level": "high",
                "context_pick_alignment": "neutral",
            },
        ]
    ).to_csv(runtime / "operator" / f"kelly_stakes_{date}.csv", index=False)

    pd.DataFrame(
        [
            {
                "prediction_date": date,
                "entity_name": "Eligible Winner",
                "market_type": "player_points",
                "selection": "under",
                "sportsbook_line": 20.5,
                "odds": -110,
                "result": "win",
                "graded_result": "win",
            },
            {
                "prediction_date": date,
                "entity_name": "Skipped Winner",
                "market_type": "player_points",
                "selection": "over",
                "sportsbook_line": 15.5,
                "odds": -120,
                "result": "win",
                "graded_result": "win",
            },
        ]
    ).to_csv(runtime / "history" / "result_feedback.csv", index=False)

    payload = build_kelly_decision_performance(prediction_date=date, out_dir=tmp_path, runtime_root=runtime)

    assert payload["overall"]["count"] == 3
    assert payload["overall"]["graded_count"] == 2
    assert payload["overall"]["pending_count"] == 1
    assert payload["by_kelly_eligible"]["true"]["wins"] == 1
    assert payload["by_kelly_eligible"]["false"]["graded_count"] == 1
    high_skip = payload["by_skip_reason"]["context_high_caution_over"]
    assert high_skip["count"] == 2
    assert high_skip["graded_count"] == 1
    assert high_skip["pending_count"] == 1
    assert high_skip["hit_rate"] == 1.0
    assert high_skip["avg_edge"] == 0.16
    assert high_skip["avg_confidence"] == 0.725
    assert payload["by_selection_side"]["over"]["count"] == 2
    assert payload["by_context_caution_level"]["high"]["count"] == 3
    assert payload["by_context_pick_alignment"]["conflicted"]["wins"] == 1

    lines = kelly_perf_log_lines(payload)
    assert "[kelly_perf] eligible graded=1 hit_rate=1.000000" in lines[0]
    assert "[kelly_perf] skipped_context_high_caution_over graded=1 hit_rate=1.000000" in lines[1]


def test_kelly_decision_performance_reports_insufficient_sample_without_grades(tmp_path) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "operator").mkdir(parents=True)
    date = "2026-04-29"
    pd.DataFrame(
        [
            {
                "prediction_date": date,
                "player_name": "Pending",
                "market_type": "player_points",
                "selection": "over",
                "line": 10.5,
                "american_odds": -110,
                "edge_pct": 0.10,
                "confidence": 0.75,
                "eligible": False,
                "skip_reason": "context_high_caution_over",
                "context_caution_level": "high",
            }
        ]
    ).to_csv(runtime / "operator" / f"kelly_stakes_{date}.csv", index=False)

    payload = build_kelly_decision_performance(prediction_date=date, out_dir=tmp_path, runtime_root=runtime)

    assert payload["overall"]["status"] == "insufficient_sample"
    assert payload["overall"]["graded_count"] == 0
    assert payload["overall"]["pending_count"] == 1
    assert "insufficient_sample" in kelly_perf_log_lines(payload)[1]


def test_market_shadow_refreshes_grading_summary_kelly_performance(tmp_path) -> None:
    runtime = tmp_path / "runtime"
    history = tmp_path / "history"
    for rel in ("operator", "history", "diagnostics"):
        (runtime / rel).mkdir(parents=True, exist_ok=True)
    history.mkdir(parents=True)
    date = "2026-04-29"

    pd.DataFrame(
        [
            {
                "prediction_date": date,
                "player_name": "Skip Loss",
                "market_type": "player_points",
                "selection": "over",
                "line": 15.5,
                "odds": -110,
                "confidence": 0.75,
                "edge": 1.0,
                "context_caution_level": "high",
                "context_pick_alignment": "conflicted",
            }
        ]
    ).to_csv(runtime / "operator" / f"full_market_board_{date}.csv", index=False)
    pd.DataFrame(
        [
            {
                "prediction_date": date,
                "player_name": "Skip Loss",
                "market_type": "player_points",
                "selection": "over",
                "line": 15.5,
                "american_odds": -110,
                "edge_pct": 0.10,
                "confidence": 0.75,
                "eligible": False,
                "skip_reason": "context_high_caution_over",
                "context_caution_level": "high",
                "context_pick_alignment": "conflicted",
            }
        ]
    ).to_csv(runtime / "operator" / f"kelly_stakes_{date}.csv", index=False)
    pd.DataFrame(
        [
            {
                "prediction_date": date,
                "entity_name": "Skip Loss",
                "market_type": "player_points",
                "selection": "over",
                "sportsbook_line": 15.5,
                "odds": -110,
                "result": "loss",
            }
        ]
    ).to_csv(runtime / "history" / "result_feedback.csv", index=False)
    summary_path = runtime / "diagnostics" / f"grading_summary_{date}.json"
    summary_path.write_text('{"overall": {"n": 1}}', encoding="utf-8")

    write_market_shadow_outputs(prediction_date=date, runtime_root=runtime, history_root=history)

    refreshed = __import__("json").loads(summary_path.read_text(encoding="utf-8"))
    high_skip = refreshed["kelly_decision_performance"]["by_skip_reason"]["context_high_caution_over"]
    assert high_skip["graded_count"] == 1
    assert high_skip["losses"] == 1
    assert high_skip["roi"] == -1.0
