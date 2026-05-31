from __future__ import annotations

import json
import subprocess
from pathlib import Path

from courtvision.reporting.shadow_rule_proposals import (
    RULE_COMBO_SHADOW_WATCHLIST,
    RULE_DATA_COLLECTION,
    RULE_DO_NOT_PROMOTE_BLOCK,
    RULE_NEAR_ELITE_WATCHLIST,
    RULE_UNDER_VISIBILITY_WATCHLIST,
    STATUS_BLOCKED_MISSING_LEARNING_REPORT,
    build_shadow_rule_proposals_report,
    write_shadow_rule_proposals_outputs,
)

PREDICTION_DATE = "2026-05-30"


def _base_item(
    bucket: str,
    *,
    recommendation: str = "WATCHLIST",
    total_rows: int = 25,
    graded_rows: int = 25,
    hit_rate: float | None = 0.6,
    roi: float | None = 0.08,
    average_odds: float | None = -110,
    break_even_hit_rate: float | None = 0.52381,
    wilson_lower_bound: float | None = 0.42,
    sample_quality_flag: str = "STRONG_SAMPLE",
    **extra,
) -> dict:
    item = {
        "bucket": bucket,
        "label": bucket,
        "total_rows": total_rows,
        "graded_rows": graded_rows,
        "pending_rows": max(total_rows - graded_rows, 0),
        "void_rows": 0,
        "wins": int((hit_rate or 0) * graded_rows),
        "losses": graded_rows - int((hit_rate or 0) * graded_rows),
        "pushes": 0,
        "hit_rate": hit_rate,
        "roi": roi,
        "average_odds": average_odds,
        "break_even_hit_rate": break_even_hit_rate,
        "wilson_lower_bound": wilson_lower_bound,
        "same_opponent_warning_rows": 0,
        "manual_review_required_rows": 0,
        "identity_conflict_rows": 0,
        "unsupported_rows": 0,
        "dirty_data_rows": 0,
        "sample_quality_flag": sample_quality_flag,
        "recommendation": recommendation,
    }
    item.update(extra)
    return item


def _write_learning_report(runtime_root: Path, payload: dict) -> Path:
    path = runtime_root / "diagnostics" / f"learning_brain_report_{PREDICTION_DATE}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _payload(**sections) -> dict:
    payload = {
        "status": "LEARNING_HEALTHY",
        "prediction_date": PREDICTION_DATE,
        "shadow_tracking_candidates": [],
        "what_the_system_has_learned": [],
        "promotion_candidates": [],
        "keep_blocked_buckets": [],
        "bucket_performance_matrix": [],
        "data_quality_warnings": [],
    }
    payload.update(sections)
    return payload


def _build(runtime_root: Path) -> dict:
    return build_shadow_rule_proposals_report(prediction_date=PREDICTION_DATE, runtime_root=runtime_root)


def test_missing_learning_brain_json_returns_blocked_without_crash(tmp_path: Path) -> None:
    payload = _build(tmp_path / "runtime")

    assert payload["status"] == STATUS_BLOCKED_MISSING_LEARNING_REPORT
    assert payload["proposals"] == []


def test_under_watchlist_proposal_is_disabled_shadow_only(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_learning_report(
        runtime_root,
        _payload(shadow_tracking_candidates=[_base_item("low-caution UNDER buckets", selection="under")]),
    )

    payload = _build(runtime_root)
    proposal = next(item for item in payload["proposals"] if item["rule_type"] == RULE_UNDER_VISIBILITY_WATCHLIST)

    assert proposal["activation"] == "DISABLED"
    assert proposal["shadow_only"] is True
    assert proposal["production_effect"] is False


def test_near_elite_proposal_is_disabled_and_human_approval_required(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_learning_report(
        runtime_root,
        _payload(shadow_tracking_candidates=[_base_item("near-elite buckets", research_lane="NEAR_ELITE_RESEARCH")]),
    )

    payload = _build(runtime_root)
    proposal = next(item for item in payload["proposals"] if item["rule_type"] == RULE_NEAR_ELITE_WATCHLIST)

    assert proposal["activation"] == "DISABLED"
    assert proposal["requires_human_approval"] is True


def test_combo_proposal_is_disabled_and_never_kelly_eligible(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_learning_report(
        runtime_root,
        _payload(
            shadow_tracking_candidates=[
                _base_item(
                    "combo OVER weak positive buckets",
                    market_type="player_points_rebounds",
                    selection="over",
                    recommendation="WATCHLIST",
                )
            ]
        ),
    )

    payload = _build(runtime_root)
    proposal = next(item for item in payload["proposals"] if item["rule_type"] == RULE_COMBO_SHADOW_WATCHLIST)

    assert proposal["activation"] == "DISABLED"
    assert proposal["eligible_for_kelly"] is False


def test_high_caution_over_keep_blocked_bucket_becomes_do_not_promote_block(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_learning_report(
        runtime_root,
        _payload(
            keep_blocked_buckets=[
                _base_item(
                    "high-caution OVERs",
                    recommendation="KEEP_BLOCKED",
                    roi=-0.15,
                    selection="over",
                    context_caution_level="high",
                )
            ]
        ),
    )

    payload = _build(runtime_root)

    assert any(item["rule_type"] == RULE_DO_NOT_PROMOTE_BLOCK for item in payload["proposals"])


def test_negative_roi_bucket_cannot_become_proposed_promotion(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_learning_report(
        runtime_root,
        _payload(
            promotion_candidates=[
                _base_item(
                    "selection=under",
                    recommendation="PROMOTION_CANDIDATE_REQUIRES_APPROVAL",
                    roi=-0.01,
                    selection="under",
                )
            ]
        ),
    )

    payload = _build(runtime_root)

    assert all(item["source_recommendation"] != "PROPOSED_ONLY_REQUIRES_APPROVAL" for item in payload["proposals"])
    assert any(item["rule_type"] == RULE_DO_NOT_PROMOTE_BLOCK for item in payload["proposals"])


def test_low_sample_bucket_becomes_data_collection_or_watchlist_only(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_learning_report(
        runtime_root,
        _payload(
            shadow_tracking_candidates=[
                _base_item(
                    "NEAR_ELITE_RESEARCH",
                    research_lane="NEAR_ELITE_RESEARCH",
                    total_rows=8,
                    graded_rows=7,
                    sample_quality_flag="LOW_SAMPLE",
                )
            ]
        ),
    )

    payload = _build(runtime_root)

    assert any(item["rule_type"] == RULE_DATA_COLLECTION for item in payload["proposals"])
    assert all(item["source_recommendation"] in {"WATCHLIST", "KEEP_SHADOW"} for item in payload["proposals"])


def test_same_opponent_warning_bucket_cannot_be_promoted(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_learning_report(
        runtime_root,
        _payload(
            promotion_candidates=[
                _base_item(
                    "same-opponent warnings",
                    recommendation="PROMOTION_CANDIDATE_REQUIRES_APPROVAL",
                    same_opponent_warning_rows=25,
                )
            ]
        ),
    )

    payload = _build(runtime_root)

    assert any(item["rule_type"] == RULE_DO_NOT_PROMOTE_BLOCK for item in payload["proposals"])
    assert all(item["source_recommendation"] != "PROPOSED_ONLY_REQUIRES_APPROVAL" for item in payload["proposals"])


def test_identity_conflict_bucket_cannot_be_promoted(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_learning_report(
        runtime_root,
        _payload(
            promotion_candidates=[
                _base_item(
                    "identity conflict rows",
                    recommendation="PROMOTION_CANDIDATE_REQUIRES_APPROVAL",
                    identity_conflict_rows=25,
                )
            ]
        ),
    )

    payload = _build(runtime_root)

    assert any(item["rule_type"] == RULE_DO_NOT_PROMOTE_BLOCK for item in payload["proposals"])
    assert all(item["source_recommendation"] != "PROPOSED_ONLY_REQUIRES_APPROVAL" for item in payload["proposals"])


def test_unsupported_market_bucket_cannot_be_promoted(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_learning_report(
        runtime_root,
        _payload(
            promotion_candidates=[
                _base_item(
                    "unsupported markets",
                    recommendation="PROMOTION_CANDIDATE_REQUIRES_APPROVAL",
                    unsupported_rows=25,
                )
            ]
        ),
    )

    payload = _build(runtime_root)

    assert any(item["rule_type"] == RULE_DO_NOT_PROMOTE_BLOCK for item in payload["proposals"])
    assert all(item["source_recommendation"] != "PROPOSED_ONLY_REQUIRES_APPROVAL" for item in payload["proposals"])


def test_json_contains_applied_changes_false(tmp_path: Path) -> None:
    payload = _build(tmp_path / "runtime")
    assert payload["applied_changes"] is False


def test_json_contains_live_rules_modified_false(tmp_path: Path) -> None:
    payload = _build(tmp_path / "runtime")
    assert payload["live_rules_modified"] is False


def test_json_contains_elite_logic_modified_false(tmp_path: Path) -> None:
    payload = _build(tmp_path / "runtime")
    assert payload["elite_logic_modified"] is False


def test_json_contains_kelly_logic_modified_false(tmp_path: Path) -> None:
    payload = _build(tmp_path / "runtime")
    assert payload["kelly_logic_modified"] is False


def test_json_contains_final_decision_modified_false(tmp_path: Path) -> None:
    payload = _build(tmp_path / "runtime")
    assert payload["final_decision_modified"] is False


def test_json_contains_pick_history_modified_false(tmp_path: Path) -> None:
    payload = _build(tmp_path / "runtime")
    assert payload["pick_history_modified"] is False


def test_json_contains_generated_real_money_recommendations_false(tmp_path: Path) -> None:
    payload = _build(tmp_path / "runtime")
    assert payload["generated_real_money_recommendations"] is False


def test_all_proposals_have_activation_disabled(tmp_path: Path) -> None:
    payload = _guardrail_payload(tmp_path)
    assert payload["proposals"]
    assert all(item["activation"] == "DISABLED" for item in payload["proposals"])


def test_all_proposals_have_production_effect_false(tmp_path: Path) -> None:
    payload = _guardrail_payload(tmp_path)
    assert all(item["production_effect"] is False for item in payload["proposals"])


def test_all_proposals_have_live_betting_false(tmp_path: Path) -> None:
    payload = _guardrail_payload(tmp_path)
    assert all(item["eligible_for_live_betting"] is False for item in payload["proposals"])


def test_all_proposals_have_kelly_false(tmp_path: Path) -> None:
    payload = _guardrail_payload(tmp_path)
    assert all(item["eligible_for_kelly"] is False for item in payload["proposals"])


def test_all_proposals_have_elite_false(tmp_path: Path) -> None:
    payload = _guardrail_payload(tmp_path)
    assert all(item["eligible_for_elite"] is False for item in payload["proposals"])


def _guardrail_payload(tmp_path: Path) -> dict:
    runtime_root = tmp_path / "runtime"
    _write_learning_report(
        runtime_root,
        _payload(
            shadow_tracking_candidates=[
                _base_item("low-caution UNDER buckets", selection="under"),
                _base_item("near-elite buckets", research_lane="NEAR_ELITE_RESEARCH"),
                _base_item("combo OVER weak positive buckets", market_type="player_points_assists", selection="over"),
            ],
            keep_blocked_buckets=[
                _base_item("broad OVERs", recommendation="DEMOTE_OR_BLOCK", roi=-0.1, selection="over")
            ],
        ),
    )
    return _build(runtime_root)


def test_runtime_outputs_are_written_only_under_runtime_root(tmp_path: Path) -> None:
    runtime_root = tmp_path / "custom_runtime"
    other_runtime = tmp_path / "outputs" / "runtime"
    _write_learning_report(runtime_root, _payload())

    txt_path, json_path, _payload_out = write_shadow_rule_proposals_outputs(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
    )

    assert txt_path == runtime_root / "operator" / f"shadow_rule_proposals_{PREDICTION_DATE}.txt"
    assert json_path == runtime_root / "diagnostics" / f"shadow_rule_proposals_{PREDICTION_DATE}.json"
    assert txt_path.exists()
    assert json_path.exists()
    assert not other_runtime.exists()


def test_generated_outputs_runtime_artifacts_are_not_tracked_by_git() -> None:
    result = subprocess.run(
        ["git", "ls-files", "outputs/runtime"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == ""
