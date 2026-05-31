from __future__ import annotations

import json
import subprocess
from pathlib import Path

from courtvision.reporting.learning_integration_snapshot import (
    STATUS_BLOCKED_BY_UNSAFE_PROPOSALS,
    STATUS_MISSING_LEARNING_REPORT,
    STATUS_MISSING_RULE_PROPOSALS,
    STATUS_READY,
    build_learning_integration_snapshot,
    render_learning_integration_snapshot_text,
    write_learning_integration_snapshot_outputs,
)

PREDICTION_DATE = "2026-05-30"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_text(path: Path, text: str = "report\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _learning_payload(**updates) -> dict:
    payload = {
        "status": "LEARNING_HEALTHY",
        "prediction_date": PREDICTION_DATE,
        "no_bet_blockers": ["No Elite/Kelly evidence available."],
        "top_losing_buckets": [
            {
                "dimension": "selection",
                "bucket": "over",
                "recommendation": "KEEP_BLOCKED",
                "total_rows": 24,
                "graded_rows": 24,
                "hit_rate": 0.42,
                "roi": -0.12,
                "sample_quality_flag": "STRONG_SAMPLE",
            }
        ],
        "top_profitable_buckets": [
            {
                "dimension": "research_lane",
                "bucket": "UNDER_ALIGNED_RESEARCH",
                "recommendation": "WATCHLIST",
                "total_rows": 26,
                "graded_rows": 24,
                "hit_rate": 0.58,
                "roi": 0.06,
                "sample_quality_flag": "STRONG_SAMPLE",
            }
        ],
        "shadow_tracking_candidates": [
            {
                "dimension": "research_lane",
                "bucket": "NEAR_ELITE_RESEARCH",
                "recommendation": "WATCHLIST",
                "total_rows": 18,
                "graded_rows": 12,
                "hit_rate": 0.58,
                "roi": 0.03,
                "sample_quality_flag": "LOW_SAMPLE",
            }
        ],
        "promotion_candidates": [],
        "keep_blocked_buckets": [
            {
                "dimension": "context_caution_level",
                "bucket": "high",
                "recommendation": "KEEP_BLOCKED",
                "total_rows": 22,
                "graded_rows": 22,
                "hit_rate": 0.41,
                "roi": -0.19,
                "sample_quality_flag": "STRONG_SAMPLE",
            }
        ],
        "recommended_core_changes": ["NO_CHANGE", "COLLECT_MORE_DATA"],
        "data_quality_warnings": [],
    }
    payload.update(updates)
    return payload


def _proposal(rule_type: str, *, activation: str = "DISABLED", production_effect: bool = False) -> dict:
    source_bucket = "selection=under" if "UNDER" in rule_type else rule_type.lower()
    return {
        "rule_id": f"{rule_type.lower()}__test",
        "rule_name": source_bucket,
        "rule_type": rule_type,
        "source_bucket": source_bucket,
        "source_recommendation": "WATCHLIST",
        "activation": activation,
        "requires_human_approval": True,
        "production_effect": production_effect,
        "eligible_for_live_betting": False,
        "eligible_for_kelly": False,
        "eligible_for_elite": False,
        "shadow_only": True,
        "reason": "test proposal",
    }


def _shadow_payload(*, proposals: list[dict] | None = None, **updates) -> dict:
    proposal_rows = proposals or [
        _proposal("UNDER_VISIBILITY_WATCHLIST"),
        _proposal("DO_NOT_PROMOTE_BLOCK"),
        _proposal("MANUAL_REVIEW_CANDIDATE_RULE"),
    ]
    counts: dict[str, int] = {}
    for proposal in proposal_rows:
        counts[proposal["rule_type"]] = counts.get(proposal["rule_type"], 0) + 1
    payload = {
        "status": "SHADOW_RULE_PROPOSALS_READY",
        "prediction_date": PREDICTION_DATE,
        "proposals": proposal_rows,
        "proposal_counts_by_type": counts,
        "disabled_proposal_count": sum(1 for proposal in proposal_rows if proposal["activation"] == "DISABLED"),
        "active_proposal_count": sum(1 for proposal in proposal_rows if proposal["activation"] != "DISABLED"),
        "production_effect_count": sum(1 for proposal in proposal_rows if proposal["production_effect"] is True),
        "human_approval_required_count": sum(
            1 for proposal in proposal_rows if proposal["requires_human_approval"] is True
        ),
        "data_quality_warnings": [],
    }
    payload.update(updates)
    return payload


def _write_learning(runtime_root: Path, payload: dict | None = None) -> None:
    _write_json(
        runtime_root / "diagnostics" / f"learning_brain_report_{PREDICTION_DATE}.json",
        payload or _learning_payload(),
    )


def _write_shadow(runtime_root: Path, payload: dict | None = None) -> None:
    _write_json(
        runtime_root / "diagnostics" / f"shadow_rule_proposals_{PREDICTION_DATE}.json",
        payload or _shadow_payload(),
    )


def _write_optional_text_reports(runtime_root: Path) -> None:
    _write_text(runtime_root / "operator" / f"learning_brain_report_{PREDICTION_DATE}.txt")
    _write_text(runtime_root / "operator" / f"shadow_rule_proposals_{PREDICTION_DATE}.txt")
    _write_text(runtime_root / "operator" / f"operator_card_{PREDICTION_DATE}.txt")
    _write_text(runtime_root / "operator" / f"daily_summary_{PREDICTION_DATE}.txt")


def _safe_runtime(tmp_path: Path) -> Path:
    runtime_root = tmp_path / "runtime"
    _write_learning(runtime_root)
    _write_shadow(runtime_root)
    _write_optional_text_reports(runtime_root)
    return runtime_root


def test_missing_both_learning_brain_and_shadow_rule_proposal_json_returns_missing_status_without_crash(
    tmp_path: Path,
) -> None:
    payload = build_learning_integration_snapshot(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
    )

    assert payload["status"] == STATUS_MISSING_LEARNING_REPORT
    warnings = "\n".join(payload["data_quality_warnings"])
    assert "Learning Brain JSON" in warnings
    assert "Shadow Rule Proposal JSON" in warnings


def test_missing_learning_brain_but_proposal_exists_returns_missing_learning_report(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_shadow(runtime_root)

    payload = build_learning_integration_snapshot(prediction_date=PREDICTION_DATE, runtime_root=runtime_root)

    assert payload["status"] == STATUS_MISSING_LEARNING_REPORT


def test_missing_proposals_but_learning_brain_exists_returns_missing_rule_proposals(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_learning(runtime_root)

    payload = build_learning_integration_snapshot(prediction_date=PREDICTION_DATE, runtime_root=runtime_root)

    assert payload["status"] == STATUS_MISSING_RULE_PROPOSALS


def test_safe_proposals_produce_ready_status(tmp_path: Path) -> None:
    runtime_root = _safe_runtime(tmp_path)

    payload = build_learning_integration_snapshot(prediction_date=PREDICTION_DATE, runtime_root=runtime_root)

    assert payload["status"] == STATUS_READY
    assert payload["total_proposals"] == 3
    assert payload["active_proposal_count"] == 0
    assert payload["production_effect_count"] == 0


def test_active_proposal_count_greater_than_zero_blocks_as_unsafe(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_learning(runtime_root)
    _write_shadow(runtime_root, _shadow_payload(proposals=[_proposal("UNDER_VISIBILITY_WATCHLIST", activation="ACTIVE")]))

    payload = build_learning_integration_snapshot(prediction_date=PREDICTION_DATE, runtime_root=runtime_root)

    assert payload["status"] == STATUS_BLOCKED_BY_UNSAFE_PROPOSALS


def test_production_effect_count_greater_than_zero_blocks_as_unsafe(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_learning(runtime_root)
    _write_shadow(
        runtime_root,
        _shadow_payload(proposals=[_proposal("UNDER_VISIBILITY_WATCHLIST", production_effect=True)]),
    )

    payload = build_learning_integration_snapshot(prediction_date=PREDICTION_DATE, runtime_root=runtime_root)

    assert payload["status"] == STATUS_BLOCKED_BY_UNSAFE_PROPOSALS


def test_json_contains_applied_changes_false(tmp_path: Path) -> None:
    runtime_root = _safe_runtime(tmp_path)
    _txt_path, json_path, _payload = write_learning_integration_snapshot_outputs(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
    )
    persisted = json.loads(json_path.read_text(encoding="utf-8"))

    assert persisted["applied_changes"] is False


def test_json_contains_live_rules_modified_false(tmp_path: Path) -> None:
    payload = build_learning_integration_snapshot(prediction_date=PREDICTION_DATE, runtime_root=_safe_runtime(tmp_path))
    assert payload["live_rules_modified"] is False


def test_json_contains_elite_logic_modified_false(tmp_path: Path) -> None:
    payload = build_learning_integration_snapshot(prediction_date=PREDICTION_DATE, runtime_root=_safe_runtime(tmp_path))
    assert payload["elite_logic_modified"] is False


def test_json_contains_kelly_logic_modified_false(tmp_path: Path) -> None:
    payload = build_learning_integration_snapshot(prediction_date=PREDICTION_DATE, runtime_root=_safe_runtime(tmp_path))
    assert payload["kelly_logic_modified"] is False


def test_json_contains_final_decision_modified_false(tmp_path: Path) -> None:
    payload = build_learning_integration_snapshot(prediction_date=PREDICTION_DATE, runtime_root=_safe_runtime(tmp_path))
    assert payload["final_decision_modified"] is False


def test_json_contains_pick_history_modified_false(tmp_path: Path) -> None:
    payload = build_learning_integration_snapshot(prediction_date=PREDICTION_DATE, runtime_root=_safe_runtime(tmp_path))
    assert payload["pick_history_modified"] is False


def test_json_contains_generated_real_money_recommendations_false(tmp_path: Path) -> None:
    payload = build_learning_integration_snapshot(prediction_date=PREDICTION_DATE, runtime_root=_safe_runtime(tmp_path))
    assert payload["generated_real_money_recommendations"] is False


def test_operator_summary_says_no_production_approval(tmp_path: Path) -> None:
    payload = build_learning_integration_snapshot(prediction_date=PREDICTION_DATE, runtime_root=_safe_runtime(tmp_path))
    text = render_learning_integration_snapshot_text(payload)

    assert payload["operator_summary"]["production_approval"] == "no"
    assert "production approval" in text.lower()
    assert "Did anything become production-approved? no" in text


def test_runtime_outputs_are_written_only_under_runtime_root(tmp_path: Path) -> None:
    runtime_root = tmp_path / "custom_runtime"
    other_runtime = tmp_path / "outputs" / "runtime"

    txt_path, json_path, _payload = write_learning_integration_snapshot_outputs(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
    )

    assert txt_path == runtime_root / "operator" / f"learning_integration_snapshot_{PREDICTION_DATE}.txt"
    assert json_path == runtime_root / "diagnostics" / f"learning_integration_snapshot_{PREDICTION_DATE}.json"
    assert txt_path.exists()
    assert json_path.exists()
    assert not other_runtime.exists()
    written_files = {path.relative_to(runtime_root).as_posix() for path in runtime_root.rglob("*") if path.is_file()}
    assert written_files == {
        f"operator/learning_integration_snapshot_{PREDICTION_DATE}.txt",
        f"diagnostics/learning_integration_snapshot_{PREDICTION_DATE}.json",
    }


def test_no_histories_are_modified(tmp_path: Path) -> None:
    runtime_root = _safe_runtime(tmp_path)
    history_root = tmp_path / "history"
    pick_history = history_root / "pick_history.csv"
    shadow_history = history_root / "shadow_candidate_lane_history.csv"
    pick_history.parent.mkdir(parents=True, exist_ok=True)
    pick_history.write_text("prediction_date,player_name\n2026-05-01,Player One\n", encoding="utf-8")
    shadow_history.write_text("prediction_date,player_name\n2026-05-01,Player Two\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (pick_history, shadow_history)}

    write_learning_integration_snapshot_outputs(prediction_date=PREDICTION_DATE, runtime_root=runtime_root)

    after = {path: path.read_bytes() for path in (pick_history, shadow_history)}
    assert after == before


def test_runtime_artifacts_are_not_tracked_by_git() -> None:
    result = subprocess.run(
        ["git", "ls-files", "outputs/runtime"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == ""
