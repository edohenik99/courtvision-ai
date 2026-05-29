from __future__ import annotations

from pathlib import Path

import pandas as pd

from courtvision.reporting.safe_action_discovery import (
    FUTURE_THRESHOLD_REVIEW,
    KEEP_BLOCKED,
    NEED_MORE_DATA,
    SHADOW_ONLY,
    build_safe_action_discovery_report,
    write_safe_action_discovery_report_outputs,
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _shadow_row(
    idx: int,
    *,
    result_status: str,
    market_type: str = "player_points",
    selection: str = "over",
    context_caution_level: str = "high",
    context_pick_alignment: str = "conflicted",
    final_elite_rejection_reason: str = "elite_reject_context_high_caution_over",
    odds: int = -110,
    edge: float = 4.0,
    confidence: float = 0.76,
    quality_score: float = 66.0,
    clv_line_points: float | str = "",
) -> dict:
    return {
        "prediction_date": "2026-05-01",
        "player_name": f"Shadow Player {idx}",
        "market_type": market_type,
        "selection": selection,
        "line": 10.5,
        "entry_odds": odds,
        "odds": odds,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "context_caution_level": context_caution_level,
        "context_pick_alignment": context_pick_alignment,
        "final_elite_rejection_reason": final_elite_rejection_reason,
        "result_status": result_status,
        "shadow_roi": 100 / abs(odds) if result_status == "hit" and odds < 0 else (-1.0 if result_status == "miss" else 0.0),
        "clv_line_points": clv_line_points,
        "fragility_score": 20.0,
        "fragility_bucket": "LOW",
        "survivability_score": 80.0,
        "survivability_bucket": "HIGH",
    }


def _paper_row(
    idx: int,
    *,
    result_status: str,
    market_type: str = "player_points_rebounds",
    selection: str = "over",
    context_caution_level: str = "high",
    context_pick_alignment: str = "conflicted",
    paper_bucket: str = "high_caution_over_watchlist",
    odds: int = -110,
) -> dict:
    return {
        "prediction_date": "2026-05-01",
        "player_name": f"Paper Player {idx}",
        "paper_bucket": paper_bucket,
        "market_type": market_type,
        "selection": selection,
        "line": 18.5,
        "odds": odds,
        "edge": 3.5,
        "directional_edge": 3.5,
        "confidence": 0.75,
        "quality_score": 70.0,
        "context_caution_level": context_caution_level,
        "context_pick_alignment": context_pick_alignment,
        "real_kelly_eligible": False,
        "result_status": result_status,
        "paper_roi": 100 / abs(odds) if result_status == "hit" and odds < 0 else (-1.0 if result_status == "miss" else 0.0),
    }


def _incubator_row(idx: int, *, result_status: str = "miss") -> dict:
    return {
        "prediction_date": "2026-05-01",
        "player": f"Incubator Player {idx}",
        "market_type": "player_points",
        "selection": "over",
        "line": 13.5,
        "odds": -112,
        "edge": 6.0,
        "confidence": 0.8,
        "quality_score": 76.0,
        "context_caution_level": "high",
        "source_rejection_reason": "elite_reject_context_high_caution_over",
        "real_money_eligible": False,
        "result_status": result_status,
    }


def _find_matrix_row(payload: dict, *, scope: str, market_type: str, selection: str, source: str) -> dict:
    matches = [
        row
        for row in payload["recommendation_matrix"]
        if row["bucket_scope"] == scope
        and row["market_type"] == market_type
        and row["selection"] == selection
        and row["history_source"] == source
    ]
    assert matches
    return matches[0]


def test_classifies_unsafe_shadow_promising_and_future_review_buckets(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    runtime_root = tmp_path / "runtime"
    unsafe_rows = [_shadow_row(i, result_status="hit" if i < 8 else "miss") for i in range(20)]
    future_review_rows = [
        _shadow_row(
            i,
            result_status="hit" if i < 35 else "miss",
            market_type="player_assists",
            selection="under",
            context_caution_level="low",
            context_pick_alignment="supports_under",
            final_elite_rejection_reason="context_aligned_shadow_candidate",
            edge=-2.5,
            confidence=0.78,
            quality_score=74.0,
            clv_line_points=0.25,
        )
        for i in range(60)
    ]
    _write_csv(history_root / "market_shadow_history.csv", [*unsafe_rows, *future_review_rows])

    paper_rows = [_paper_row(i, result_status="hit" if i < 30 else "miss") for i in range(50)]
    _write_csv(history_root / "paper_kelly_history.csv", paper_rows)

    incubator_rows = [_incubator_row(i) for i in range(5)]
    _write_csv(history_root / "incubator_history.csv", incubator_rows)

    payload, matrix_df = build_safe_action_discovery_report(
        prediction_date="2026-05-02",
        runtime_root=runtime_root,
        history_root=history_root,
        generated_at_utc="2026-05-02T00:00:00+00:00",
    )

    assert payload["read_only"] is True
    assert payload["betting_logic_changed"] is False
    assert payload["real_money_promotion_recommended"] is False
    assert not matrix_df.empty

    unsafe = _find_matrix_row(
        payload,
        scope="source_reason",
        market_type="player_points",
        selection="over",
        source="market_shadow_history",
    )
    assert unsafe["recommendation"] == KEEP_BLOCKED
    assert unsafe["classification"] == "unsafe_negative_roi"

    high_caution_positive = _find_matrix_row(
        payload,
        scope="source_reason",
        market_type="player_points_rebounds",
        selection="over",
        source="paper_kelly_history",
    )
    assert high_caution_positive["recommendation"] == SHADOW_ONLY
    assert high_caution_positive["classification"] == "promising_but_gate_blocked"

    future_review = _find_matrix_row(
        payload,
        scope="source_reason",
        market_type="player_assists",
        selection="under",
        source="market_shadow_history",
    )
    assert future_review["recommendation"] == FUTURE_THRESHOLD_REVIEW
    assert future_review["classification"] == "promising_with_moderate_evidence"

    incubator = _find_matrix_row(
        payload,
        scope="source_reason",
        market_type="player_points",
        selection="over",
        source="incubator_history",
    )
    assert incubator["recommendation"] == NEED_MORE_DATA
    assert payload["potential_safe_action_discovery_candidates"]
    assert payload["buckets_that_should_remain_blocked"]
    assert payload["buckets_that_need_more_samples"]


def test_near_elite_artifacts_are_pending_review_only(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    rows = [
        {
            "prediction_date": "2026-05-02",
            "player_name": "Near Elite Player",
            "market_type": "player_points",
            "selection": "over",
            "line": 12.5,
            "odds": -110,
            "edge": 4.0,
            "confidence": 0.72,
            "quality_score": 60.0,
            "context_caution_level": "high",
            "context_pick_alignment": "conflicted",
            "final_elite_rejection_reason": "elite_reject_context_high_caution_over",
            "operator_action": "REVIEW_ONLY",
            "stake_policy": "NO_AUTO_STAKE",
            "kelly_eligible": False,
            "review_lane": "near_elite",
        }
    ]
    _write_csv(runtime_root / "operator" / "near_elite_review_2026-05-02.csv", rows)

    payload, _matrix_df = build_safe_action_discovery_report(
        prediction_date="2026-05-02",
        runtime_root=runtime_root,
        history_root=history_root,
        generated_at_utc="2026-05-02T00:00:00+00:00",
    )

    assert payload["summary"]["near_elite_artifact_rows"] == 1
    near_elite_rows = payload["near_elite_bucket_performance"]
    assert len(near_elite_rows) == 1
    assert near_elite_rows[0]["graded_rows"] == 0
    assert near_elite_rows[0]["recommendation"] == NEED_MORE_DATA


def test_write_outputs_is_read_only_for_history(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    pick_history = history_root / "pick_history.csv"
    rows = [
        {
            "prediction_date": "2026-05-01",
            "player_name": "Historical Pick",
            "market": "player_points",
            "selection": "under",
            "line": 20.5,
            "odds": -110,
            "edge": -2.0,
            "confidence": 0.75,
            "quality_score": 65.0,
            "qualification_reason": "player_points_high_quality_pass",
            "context_caution_level": "low",
            "context_pick_alignment": "supports_under",
            "result_status": "hit",
            "kelly_eligible": True,
        }
    ]
    _write_csv(pick_history, rows)
    original_history = pick_history.read_text(encoding="utf-8")

    text_path, json_path, csv_path, payload = write_safe_action_discovery_report_outputs(
        prediction_date="2026-05-02",
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert text_path.exists()
    assert json_path.exists()
    assert csv_path.exists()
    assert payload["read_only"] is True
    assert payload["betting_logic_changed"] is False
    assert pick_history.read_text(encoding="utf-8") == original_history


def test_under_vs_over_and_clv_warnings_are_reported(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    runtime_root = tmp_path / "runtime"
    over_rows = [_shadow_row(i, result_status="hit" if i < 12 else "miss") for i in range(25)]
    under_rows = [
        _shadow_row(
            i,
            result_status="hit" if i < 14 else "miss",
            market_type="player_rebounds",
            selection="under",
            context_caution_level="low",
            context_pick_alignment="supports_under",
            final_elite_rejection_reason="context_aligned_shadow_candidate",
            edge=-1.8,
        )
        for i in range(25)
    ]
    _write_csv(history_root / "market_shadow_history.csv", [*over_rows, *under_rows])

    payload, _matrix_df = build_safe_action_discovery_report(
        prediction_date="2026-05-02",
        runtime_root=runtime_root,
        history_root=history_root,
        generated_at_utc="2026-05-02T00:00:00+00:00",
    )

    selections = {row["selection"] for row in payload["under_vs_over_comparison"]}
    assert {"over", "under"}.issubset(selections)
    assert payload["sample_size_warnings"]
    assert payload["clv_availability_warnings"]
