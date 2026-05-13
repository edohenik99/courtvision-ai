import json
from pathlib import Path

import pandas as pd

from courtvision.streamlit_review_artifacts import (
    PHASE15_REVIEW_ARTIFACTS,
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
    for phase in payload["phases"].values():
        assert phase["text"] == ""
        assert phase["text_record"]["status"] == "missing"
        assert phase["csv"].empty
        assert phase["csv_record"]["status"] == "missing"
