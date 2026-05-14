import json
from pathlib import Path

import pandas as pd

from courtvision.streamlit_review_artifacts import (
    COMPLETION_STATE_ARTIFACTS,
    PHASE15_REVIEW_ARTIFACTS,
    extract_completion_state_summary,
    extract_quality_review_statuses,
    load_quality_review_artifacts,
    quality_review_artifact_paths,
)


def test_quality_review_artifact_paths_resolve_under_runtime_operator(tmp_path: Path) -> None:
    paths = quality_review_artifact_paths("outputs", "2026/05/13", repo_root=tmp_path)

    assert paths["quality_summary_text"] == (
        tmp_path / "outputs" / "runtime" / "operator" / "quality_summary_2026-05-13.txt"
    )
    assert paths["quality_summary_json"] == (
        tmp_path / "outputs" / "runtime" / "operator" / "quality_summary_2026-05-13.json"
    )
    assert paths["completion_state_audit_json"] == (
        tmp_path / "outputs" / "runtime" / "diagnostics" / "completion_state_audit_2026-05-13.json"
    )
    assert paths["completion_state_audit_text"] == (
        tmp_path / "outputs" / "runtime" / "operator" / "completion_state_audit_2026-05-13.txt"
    )
    for key, spec in COMPLETION_STATE_ARTIFACTS.items():
        assert paths[key].name == spec["template"].format(date="2026-05-13")
    for phase_key, spec in PHASE15_REVIEW_ARTIFACTS.items():
        assert paths[f"{phase_key}_text"].name == spec["text_template"].format(date="2026-05-13")
        assert paths[f"{phase_key}_csv"].name == spec["csv_template"].format(date="2026-05-13")


def test_quality_review_artifacts_load_text_json_and_csv_safely(tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    operator = out_dir / "runtime" / "operator"
    operator.mkdir(parents=True)
    prediction_date = "2026-05-13"

    (operator / f"quality_summary_{prediction_date}.txt").write_text(
        "QUALITY SUMMARY\nrun_health_status: HEALTHY\n",
        encoding="utf-8",
    )
    (operator / f"quality_summary_{prediction_date}.json").write_text(
        json.dumps(
            {
                "run_health": {"status": "HEALTHY"},
                "candidate_funnel": {
                    "elite_board_count": 3,
                    "full_market_board_count": 58,
                },
                "kelly_safety_summary": {"kelly_eligible_count": 2},
                "low_line_over_minutes_guard_review": {
                    "readiness_verdict": "REVIEW_READY_WEAK_MINUTES_PRESENT",
                },
            }
        ),
        encoding="utf-8",
    )
    diagnostics = out_dir / "runtime" / "diagnostics"
    diagnostics.mkdir(parents=True)
    (diagnostics / f"completion_state_audit_{prediction_date}.json").write_text(
        json.dumps(
            {
                "report_agreement_status": "COMPLETE_WITH_SHADOW_OPEN_NOISE",
                "real_pick_rows": 1,
                "real_pick_pending_count": 0,
                "real_pick_graded_count": 1,
                "shadow_pending_count": 57,
                "paper_pending_count": 25,
                "agreement_issues": [],
                "warnings": [],
                "interpretation": "Real picks are fully graded.",
            }
        ),
        encoding="utf-8",
    )
    (operator / f"completion_state_audit_{prediction_date}.txt").write_text(
        "Completion State Audit\n",
        encoding="utf-8",
    )
    (operator / f"low_line_over_minutes_guard_review_{prediction_date}.txt").write_text(
        "LOW-LINE OVER MINUTES GUARD REVIEW\n",
        encoding="utf-8",
    )
    pd.DataFrame(
        [{"player_name": "Example Player", "minutes_bucket": "weak_minutes_basis"}]
    ).to_csv(operator / f"low_line_over_minutes_guard_review_{prediction_date}.csv", index=False)

    payload = load_quality_review_artifacts(out_dir, prediction_date)
    statuses = extract_quality_review_statuses(payload["quality_summary_json"])

    assert "QUALITY SUMMARY" in payload["quality_summary_text"]
    assert payload["quality_summary_json"]["run_health"]["status"] == "HEALTHY"
    assert payload["completion_state_summary"]["status"] == "COMPLETE_WITH_SHADOW_OPEN_NOISE"
    assert payload["completion_state_summary"]["status_state"] == "info"
    assert payload["completion_state_summary"]["real_pick_pending_count"] == 0
    assert "Completion State Audit" in payload["completion_state_audit_text"]
    assert statuses["run_health"] == "HEALTHY"
    assert statuses["elite_count"] == 3
    assert statuses["full_market_count"] == 58
    assert statuses["kelly_eligible_count"] == 2
    assert statuses["phase15d_review_readiness_verdict"] == "REVIEW_READY_WEAK_MINUTES_PRESENT"
    review_phase = payload["phases"]["phase15d_review"]
    assert "LOW-LINE OVER MINUTES GUARD REVIEW" in review_phase["text"]
    assert len(review_phase["csv"]) == 1
    assert review_phase["csv_record"]["status"] == "loaded"


def test_quality_review_artifacts_missing_files_are_non_fatal(tmp_path: Path) -> None:
    payload = load_quality_review_artifacts(tmp_path / "outputs", "2026-05-13")

    assert payload["quality_summary_text"] == ""
    assert payload["quality_summary_json"] == {}
    assert payload["quality_summary_text_record"]["status"] == "missing"
    assert payload["quality_summary_json_record"]["status"] == "missing"
    assert payload["completion_state_audit_json"] == {}
    assert payload["completion_state_audit_json_record"]["status"] == "missing"
    assert payload["completion_state_summary"]["available"] is False
    assert payload["completion_state_summary"]["notice"] == "Completion audit not found for this date."
    for phase in payload["phases"].values():
        assert phase["text"] == ""
        assert phase["text_record"]["status"] == "missing"
        assert phase["csv"].empty
        assert phase["csv_record"]["status"] == "missing"


def test_completion_state_summary_handles_missing_artifact() -> None:
    summary = extract_completion_state_summary({}, {"status": "missing", "path": "missing.json"})

    assert summary["available"] is False
    assert summary["notice"] == "Completion audit not found for this date."
    assert summary["status_label"] == "Not available"


def test_completion_state_summary_counts_issues_and_warnings() -> None:
    summary = extract_completion_state_summary(
        {
            "report_agreement_status": "STALE_PENDING_RISK",
            "real_pick_rows": 2,
            "real_pick_pending_count": 0,
            "real_pick_graded_count": 2,
            "shadow_pending_count": 1,
            "paper_pending_count": 0,
            "agreement_issues": ["issue"],
            "warnings": ["warning one", "warning two"],
        }
    )

    assert summary["available"] is True
    assert summary["status_label"] == "Stale pending risk"
    assert summary["status_state"] == "danger"
    assert summary["agreement_issues_count"] == 1
    assert summary["warnings_count"] == 2
