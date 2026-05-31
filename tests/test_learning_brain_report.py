from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd

from courtvision.reporting.learning_brain import (
    RECOMMEND_KEEP_BLOCKED,
    RECOMMEND_PROMOTION_REQUIRES_APPROVAL,
    RECOMMEND_WATCHLIST,
    STATUS_LEARNING_BLOCKED_BY_MISSING_HISTORY,
    STATUS_LEARNING_NEEDS_MORE_DATA,
    build_learning_brain_report,
    write_learning_brain_report_outputs,
)

PREDICTION_DATE = "2026-05-30"


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _history_row(
    idx: int,
    *,
    prediction_date: str | None = None,
    player_prefix: str = "Player",
    source: str = "shadow_candidate_lane_history",
    lane: str = "TEST_RESEARCH",
    market_type: str = "player_points",
    selection: str = "over",
    result_status: str = "hit",
    odds: int = -110,
    line: float | None = None,
    edge: float = 2.5,
    confidence: float = 0.76,
    quality_score: float = 82.0,
    context_caution_level: str = "low",
    context_pick_alignment: str = "aligned",
    same_opponent_warning: bool = False,
    manual_review_required: bool = False,
    identity_resolution_category: str = "matched",
    source_artifact_date: str | None = None,
    reason_not_real_kelly: str = "",
) -> dict:
    date = prediction_date or f"2026-05-{1 + idx:02d}"
    row = {
        "prediction_date": date,
        "source_artifact_date": source_artifact_date or date,
        "player": f"{player_prefix} {idx}",
        "player_name": f"{player_prefix} {idx}",
        "market_type": market_type,
        "market": market_type,
        "selection": selection,
        "line": line if line is not None else 10.5 + idx,
        "odds": odds,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "context_caution_level": context_caution_level,
        "context_pick_alignment": context_pick_alignment,
        "research_lane": lane,
        "lane": lane,
        "source_rejection_reason": "test_reason",
        "manual_review_required": manual_review_required,
        "same_opponent_warning": same_opponent_warning,
        "same_opponent_under_warning": same_opponent_warning,
        "identity_resolution_category": identity_resolution_category,
        "reason_not_real_kelly": reason_not_real_kelly,
        "result_status": result_status,
        "actual_value": 12 if result_status in {"hit", "miss", "push"} else "",
        "hit": result_status == "hit",
        "miss": result_status == "miss",
        "push": result_status == "push",
        "flat_profit_loss": 0.909091 if result_status == "hit" else (-1.0 if result_status == "miss" else 0.0),
    }
    if source == "pick_history":
        row["player_name"] = row.pop("player")
        row["market"] = market_type
        row["qualification_reason"] = lane
    if source == "paper_kelly_history":
        row["paper_bucket"] = lane
        row["paper_roi"] = row["flat_profit_loss"]
    return row


def _write_history(history_root: Path, filename: str, rows: list[dict]) -> None:
    _write_csv(history_root / filename, rows)


def _profitable_rows(count: int = 25, wins: int = 20, **kwargs) -> list[dict]:
    rows: list[dict] = []
    for idx in range(count):
        rows.append(_history_row(idx, result_status="hit" if idx < wins else "miss", **kwargs))
    return rows


def _find_bucket(payload: dict, *, dimension: str, bucket: str) -> dict:
    for item in payload["bucket_performance_matrix"]:
        if item["dimension"] == dimension and item["bucket"] == bucket:
            return item
    raise AssertionError(f"missing bucket {dimension}={bucket}")


def test_missing_all_histories_returns_blocked_or_needs_more_data(tmp_path: Path) -> None:
    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
    )

    assert payload["status"] in {
        STATUS_LEARNING_BLOCKED_BY_MISSING_HISTORY,
        STATUS_LEARNING_NEEDS_MORE_DATA,
    }


def test_low_sample_profitable_bucket_returns_watchlist_not_promotion(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    _write_history(history_root, "shadow_candidate_lane_history.csv", _profitable_rows(count=6, wins=5))

    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=history_root,
        min_sample=20,
    )

    lane_bucket = _find_bucket(payload, dimension="research_lane", bucket="TEST_RESEARCH")
    assert lane_bucket["recommendation"] == RECOMMEND_WATCHLIST
    assert payload["promotion_candidates"] == []


def test_profitable_enough_bucket_can_only_require_approval(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    _write_history(history_root, "market_shadow_history.csv", _profitable_rows(count=25, wins=20, lane="FULL_MARKET_SHADOW"))

    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=history_root,
        min_sample=20,
    )

    assert payload["promotion_candidates"]
    assert {item["recommendation"] for item in payload["promotion_candidates"]} == {
        RECOMMEND_PROMOTION_REQUIRES_APPROVAL
    }
    assert payload["generated_real_money_recommendations"] is False
    assert payload["applied_changes"] is False


def test_negative_roi_high_caution_over_bucket_returns_keep_blocked(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    rows = [
        _history_row(
            idx,
            result_status="miss",
            selection="over",
            context_caution_level="high",
            context_pick_alignment="conflicted",
        )
        for idx in range(20)
    ]
    _write_history(history_root, "shadow_candidate_lane_history.csv", rows)

    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=history_root,
        min_sample=20,
    )

    high_caution = next(item for item in payload["keep_blocked_buckets"] if item["bucket"] == "high-caution OVERs")
    assert high_caution["recommendation"] == RECOMMEND_KEEP_BLOCKED


def test_under_research_stays_shadow_until_enough_then_requires_approval(tmp_path: Path) -> None:
    low_history = tmp_path / "low_history"
    _write_history(
        low_history,
        "shadow_candidate_lane_history.csv",
        _profitable_rows(count=6, wins=5, selection="under", lane="UNDER_ALIGNED_RESEARCH"),
    )

    low_payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime_low",
        history_root=low_history,
        min_sample=20,
    )
    assert low_payload["promotion_candidates"] == []

    enough_history = tmp_path / "enough_history"
    _write_history(
        enough_history,
        "shadow_candidate_lane_history.csv",
        _profitable_rows(count=25, wins=20, selection="under", lane="UNDER_ALIGNED_RESEARCH"),
    )

    enough_payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime_enough",
        history_root=enough_history,
        min_sample=20,
    )
    assert enough_payload["promotion_candidates"]
    assert {item["recommendation"] for item in enough_payload["promotion_candidates"]} == {
        RECOMMEND_PROMOTION_REQUIRES_APPROVAL
    }
    assert enough_payload["generated_real_money_recommendations"] is False


def test_same_opponent_warning_bucket_cannot_auto_promote(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    _write_history(
        history_root,
        "market_shadow_history.csv",
        _profitable_rows(count=25, wins=25, same_opponent_warning=True),
    )

    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=history_root,
        min_sample=20,
    )

    assert payload["promotion_candidates"] == []
    warning_bucket = _find_bucket(payload, dimension="same_opponent_warning", bucket="True")
    assert warning_bucket["recommendation"] == RECOMMEND_KEEP_BLOCKED


def test_identity_conflict_bucket_cannot_auto_promote(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    _write_history(
        history_root,
        "market_shadow_history.csv",
        _profitable_rows(count=25, wins=25, identity_resolution_category="source_identity_conflict"),
    )

    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=history_root,
        min_sample=20,
    )

    assert payload["promotion_candidates"] == []
    conflict_bucket = _find_bucket(payload, dimension="identity_resolution_category", bucket="source_identity_conflict")
    assert conflict_bucket["recommendation"] == RECOMMEND_KEEP_BLOCKED


def test_unsupported_market_bucket_cannot_auto_promote(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    _write_history(
        history_root,
        "market_shadow_history.csv",
        _profitable_rows(count=25, wins=25, market_type="unsupported_market"),
    )

    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=history_root,
        min_sample=20,
    )

    assert payload["promotion_candidates"] == []
    unsupported_bucket = _find_bucket(payload, dimension="market_type", bucket="unsupported_market")
    assert unsupported_bucket["recommendation"] == RECOMMEND_KEEP_BLOCKED


def test_report_never_writes_pick_history(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    pick_history = history_root / "pick_history.csv"
    _write_history(history_root, "pick_history.csv", [_history_row(1, source="pick_history")])
    before = pick_history.read_bytes()

    write_learning_brain_report_outputs(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=history_root,
    )

    assert pick_history.read_bytes() == before


def test_report_never_modifies_history_files(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    files = {
        "pick_history.csv": [_history_row(1, source="pick_history")],
        "incubator_history.csv": [_history_row(2, source="incubator_history")],
        "shadow_candidate_lane_history.csv": [_history_row(3)],
        "market_shadow_history.csv": [_history_row(4, source="market_shadow_history")],
        "paper_kelly_history.csv": [_history_row(5, source="paper_kelly_history")],
    }
    for filename, rows in files.items():
        _write_history(history_root, filename, rows)
    before = {filename: (history_root / filename).read_bytes() for filename in files}

    write_learning_brain_report_outputs(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=history_root,
    )

    after = {filename: (history_root / filename).read_bytes() for filename in files}
    assert after == before


def test_report_never_modifies_elite_kelly_or_final_decision_files(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    _write_csv(operator / f"elite_board_{PREDICTION_DATE}.csv", [{"player_name": "Elite"}])
    _write_csv(operator / f"full_market_board_{PREDICTION_DATE}.csv", [{"player_name": "Candidate"}])
    card_path = operator / f"operator_card_{PREDICTION_DATE}.txt"
    card_path.write_text("final_decision: NO BET\nKelly eligible count: 0\n", encoding="utf-8")
    snapshots = {
        "elite": (operator / f"elite_board_{PREDICTION_DATE}.csv").read_bytes(),
        "full": (operator / f"full_market_board_{PREDICTION_DATE}.csv").read_bytes(),
        "card": card_path.read_bytes(),
        "kelly_source": Path("courtvision/betting/kelly.py").read_bytes(),
    }

    write_learning_brain_report_outputs(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=tmp_path / "history",
    )

    assert (operator / f"elite_board_{PREDICTION_DATE}.csv").read_bytes() == snapshots["elite"]
    assert (operator / f"full_market_board_{PREDICTION_DATE}.csv").read_bytes() == snapshots["full"]
    assert card_path.read_bytes() == snapshots["card"]
    assert Path("courtvision/betting/kelly.py").read_bytes() == snapshots["kelly_source"]


def test_json_contains_applied_changes_false(tmp_path: Path) -> None:
    _, json_path, payload = write_learning_brain_report_outputs(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
    )
    persisted = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["applied_changes"] is False
    assert persisted["applied_changes"] is False


def test_json_contains_pick_history_modified_false(tmp_path: Path) -> None:
    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
    )
    assert payload["pick_history_modified"] is False


def test_json_contains_live_rules_modified_false(tmp_path: Path) -> None:
    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
    )
    assert payload["live_rules_modified"] is False


def test_json_contains_generated_real_money_recommendations_false(tmp_path: Path) -> None:
    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
    )
    assert payload["generated_real_money_recommendations"] is False


def test_missing_bet_readiness_and_no_bet_artifacts_warn_without_crash(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    _write_history(history_root, "market_shadow_history.csv", [_history_row(1)])

    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=history_root,
    )

    warnings = "\n".join(payload["data_quality_warnings"]).lower()
    assert "bet_readiness_report" in warnings
    assert "no_bet_funnel_report" in warnings


def test_pending_heavy_bucket_gets_data_quality_warning(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    rows = [_history_row(idx, result_status="pending") for idx in range(20)]
    rows.extend(_history_row(100 + idx, result_status="hit") for idx in range(2))
    _write_history(history_root, "shadow_candidate_lane_history.csv", rows)

    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=history_root,
    )

    assert any("pending-heavy" in warning.lower() for warning in payload["data_quality_warnings"])


def test_duplicate_rows_are_detected_and_reported(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    row = _history_row(1, prediction_date="2026-05-01", player_prefix="Duplicate", line=12.5)
    _write_history(history_root, "market_shadow_history.csv", [row])
    _write_history(history_root, "shadow_candidate_lane_history.csv", [row.copy()])

    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=history_root,
    )

    assert payload["duplicate_rows_detected"] == 1
    assert any("duplicate rows detected" in warning for warning in payload["data_quality_warnings"])


def test_paper_kelly_is_corroborating_and_not_double_counted_for_promotion(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    primary_rows = _profitable_rows(count=6, wins=5, player_prefix="Primary")
    paper_rows = _profitable_rows(
        count=25,
        wins=25,
        player_prefix="Paper",
        source="paper_kelly_history",
        lane="paper_kelly_strong",
    )
    _write_history(history_root, "market_shadow_history.csv", primary_rows)
    _write_history(history_root, "paper_kelly_history.csv", paper_rows)

    payload = build_learning_brain_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=history_root,
        min_sample=20,
    )

    assert payload["total_samples_by_source"]["paper_kelly_history"] == 25
    assert payload["primary_combined_sample_count"] == 6
    assert payload["promotion_candidates"] == []
    paper_summary = next(item for item in payload["source_summaries"] if item["source"] == "paper_kelly_history")
    assert paper_summary["source_role"] == "corroborating"


def test_generated_outputs_are_written_only_to_requested_runtime_root(tmp_path: Path) -> None:
    runtime_root = tmp_path / "custom_runtime"
    other_runtime = tmp_path / "outputs" / "runtime"

    txt_path, json_path, _payload = write_learning_brain_report_outputs(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=tmp_path / "history",
    )

    assert txt_path == runtime_root / "operator" / f"learning_brain_report_{PREDICTION_DATE}.txt"
    assert json_path == runtime_root / "diagnostics" / f"learning_brain_report_{PREDICTION_DATE}.json"
    assert txt_path.exists()
    assert json_path.exists()
    assert not other_runtime.exists()


def test_runtime_artifacts_are_not_tracked_by_git() -> None:
    result = subprocess.run(
        ["git", "ls-files", "outputs/runtime"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == ""
