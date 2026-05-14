from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_historical_cockpit import (
    FAIL_UNREADABLE,
    FAIL_PENDING_REAL_PICKS,
    PASS,
    PASS_NO_SLATE,
    WARN_AUDIT_ISSUES,
    WARN_MISSING_RECOMMENDED_ACTION,
    WARN_MISSING_ARTIFACTS,
    build_historical_cockpit_validation,
    write_historical_cockpit_validation,
)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_cockpit_artifacts(
    runtime_root: Path,
    prediction_date: str,
    *,
    final_decision: str = "BETTABLE",
    games_count: int = 1,
    report_agreement_status: str = "COMPLETE",
    real_pick_pending_count: int = 0,
    recommended_action: str | None = "slate closed / no action required",
    warnings: list[str] | None = None,
    agreement_issues: list[str] | None = None,
    missing: set[str] | None = None,
) -> dict[str, Path]:
    missing = missing or set()
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    paths = {
        "operator_card": operator / f"operator_card_{prediction_date}.txt",
        "completion_audit_text": operator / f"completion_state_audit_{prediction_date}.txt",
        "completion_audit_json": diagnostics / f"completion_state_audit_{prediction_date}.json",
        "daily_summary": operator / f"daily_summary_{prediction_date}.txt",
        "quality_summary": operator / f"quality_summary_{prediction_date}.txt",
    }

    if "operator_card" not in missing:
        _write_text(
            paths["operator_card"],
            "\n".join(
                [
                    f"COURTVISION DAILY CARD - {prediction_date}",
                    f"final_decision: {final_decision}",
                    "",
                    "Slate Summary",
                    "----------------------------------------",
                    f"- games count: {games_count}",
                    "",
                    "Completion State Audit",
                    "----------------------------------------",
                    *(
                        [f"- recommended action: {recommended_action}"]
                        if recommended_action is not None
                        else []
                    ),
                    "",
                ]
            ),
        )
    if "completion_audit_text" not in missing:
        _write_text(paths["completion_audit_text"], f"Completion State Audit - {prediction_date}\n")
    if "completion_audit_json" not in missing:
        _write_json(
            paths["completion_audit_json"],
            {
                "report_agreement_status": report_agreement_status,
                "real_pick_pending_count": real_pick_pending_count,
                "warnings": warnings or [],
                "agreement_issues": agreement_issues or [],
            },
        )
    if "daily_summary" not in missing:
        _write_text(paths["daily_summary"], f"Daily Summary - {prediction_date}\n")
    if "quality_summary" not in missing:
        _write_text(paths["quality_summary"], f"Quality Summary - {prediction_date}\n")
    return paths


def _only_row(payload: dict) -> dict:
    assert len(payload["dates"]) == 1
    return payload["dates"][0]


def test_clean_complete_slate_passes(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    prediction_date = "2026-05-10"
    _seed_cockpit_artifacts(runtime_root, prediction_date)

    payload = build_historical_cockpit_validation(
        start_date=prediction_date,
        end_date=prediction_date,
        runtime_root=runtime_root,
    )
    row = _only_row(payload)

    assert row["validation_status"] == PASS
    assert row["operator_card_exists"] is True
    assert row["completion_audit_json_exists"] is True
    assert row["report_agreement_status"] == "COMPLETE"
    assert row["real_pick_pending_count"] == 0
    assert row["recommended_action"] == "slate closed / no action required"
    assert payload["summary"]["pass_count"] == 1


def test_no_slate_day_passes_as_pass_no_slate(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    prediction_date = "2026-05-14"
    _seed_cockpit_artifacts(
        runtime_root,
        prediction_date,
        final_decision="NO BET",
        games_count=0,
        report_agreement_status="COMPLETE",
        real_pick_pending_count=0,
        recommended_action="slate closed / no action required",
    )

    row = _only_row(
        build_historical_cockpit_validation(
            start_date=prediction_date,
            end_date=prediction_date,
            runtime_root=runtime_root,
        )
    )

    assert row["validation_status"] == PASS_NO_SLATE
    assert row["final_decision"] == "NO BET"
    assert row["games_count"] == 0


def test_missing_operator_card_warns(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    prediction_date = "2026-05-11"
    _seed_cockpit_artifacts(runtime_root, prediction_date, missing={"operator_card"})

    row = _only_row(
        build_historical_cockpit_validation(
            start_date=prediction_date,
            end_date=prediction_date,
            runtime_root=runtime_root,
        )
    )

    assert row["validation_status"] == WARN_MISSING_ARTIFACTS
    assert row["operator_card_exists"] is False
    assert row["missing_artifacts"] == ["operator_card"]


def test_missing_completion_audit_json_warns(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    prediction_date = "2026-05-12"
    _seed_cockpit_artifacts(runtime_root, prediction_date, missing={"completion_audit_json"})

    row = _only_row(
        build_historical_cockpit_validation(
            start_date=prediction_date,
            end_date=prediction_date,
            runtime_root=runtime_root,
        )
    )

    assert row["validation_status"] == WARN_MISSING_ARTIFACTS
    assert row["completion_audit_json_exists"] is False
    assert row["missing_artifacts"] == ["completion_audit_json"]


def test_real_pending_count_fails(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    prediction_date = "2026-05-13"
    _seed_cockpit_artifacts(
        runtime_root,
        prediction_date,
        report_agreement_status="PARTIAL",
        real_pick_pending_count=2,
        recommended_action="inspect grading before trusting results",
    )

    row = _only_row(
        build_historical_cockpit_validation(
            start_date=prediction_date,
            end_date=prediction_date,
            runtime_root=runtime_root,
        )
    )

    assert row["validation_status"] == FAIL_PENDING_REAL_PICKS
    assert row["real_pick_pending_count"] == 2


def test_warning_or_agreement_issue_warns(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    prediction_date = "2026-05-15"
    _seed_cockpit_artifacts(
        runtime_root,
        prediction_date,
        warnings=["source warning"],
        agreement_issues=["daily_summary_pending_grading_mismatch"],
        recommended_action="inspect completion audit before trusting results",
    )

    row = _only_row(
        build_historical_cockpit_validation(
            start_date=prediction_date,
            end_date=prediction_date,
            runtime_root=runtime_root,
        )
    )

    assert row["validation_status"] == WARN_AUDIT_ISSUES
    assert row["warning_count"] == 1
    assert row["agreement_issue_count"] == 1


def test_stale_operator_card_missing_recommended_action_warns(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    prediction_date = "2026-05-17"
    _seed_cockpit_artifacts(runtime_root, prediction_date, recommended_action=None)

    text_path, _json_path, _csv_path, payload = write_historical_cockpit_validation(
        start_date=prediction_date,
        end_date=prediction_date,
        runtime_root=runtime_root,
    )
    row = _only_row(payload)

    assert row["validation_status"] == WARN_MISSING_RECOMMENDED_ACTION
    assert row["operator_card_exists"] is True
    assert row["report_agreement_status"] == "COMPLETE"
    assert row["real_pick_pending_count"] == 0
    assert row["final_decision"] == "BETTABLE"
    assert row["recommended_action"] is None
    assert row["missing_operator_card_fields"] == ["recommended_action"]
    assert row["parse_errors"] == []
    text = text_path.read_text(encoding="utf-8")
    assert "WARN_MISSING_RECOMMENDED_ACTION" in text
    assert f"py -3.13 scripts/write_operator_card.py --prediction-date {prediction_date}" in text


def test_bad_completion_audit_json_fails_unreadable(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    prediction_date = "2026-05-18"
    paths = _seed_cockpit_artifacts(runtime_root, prediction_date)
    paths["completion_audit_json"].write_text("{not valid json", encoding="utf-8")

    row = _only_row(
        build_historical_cockpit_validation(
            start_date=prediction_date,
            end_date=prediction_date,
            runtime_root=runtime_root,
        )
    )

    assert row["validation_status"] == FAIL_UNREADABLE
    assert row["completion_audit_json_exists"] is True
    assert any("Could not parse" in error for error in row["parse_errors"])


def test_writer_is_read_only_for_existing_cockpit_artifacts(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    prediction_date = "2026-05-16"
    paths = _seed_cockpit_artifacts(runtime_root, prediction_date)
    before = {key: path.read_bytes() for key, path in paths.items()}

    text_path, json_path, csv_path, payload = write_historical_cockpit_validation(
        start_date=prediction_date,
        end_date=prediction_date,
        runtime_root=runtime_root,
    )

    after = {key: path.read_bytes() for key, path in paths.items()}
    assert after == before
    assert text_path.exists()
    assert json_path.exists()
    assert csv_path.exists()
    assert payload["read_only"] is True
    assert "Historical Cockpit Validation - 2026-05-16..2026-05-16" in text_path.read_text(encoding="utf-8")
