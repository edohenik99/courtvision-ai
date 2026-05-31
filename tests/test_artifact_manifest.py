import json
from pathlib import Path

from courtvision.reporting.artifact_manifest import (
    SEVERITY_FATAL,
    SEVERITY_INFORMATIONAL,
    SEVERITY_SHADOW_ONLY,
    SEVERITY_WARNING,
    build_artifact_manifest,
    write_artifact_manifest_outputs,
)


PREDICTION_DATE = "2026-04-10"


def _artifact(manifest: dict, name: str) -> dict:
    return next(item for item in manifest["artifacts"] if item["name"] == name)


def _write_text(path: Path, text: str = "ok\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _shadow_payload(
    *,
    prediction_date: str = PREDICTION_DATE,
    runtime_root: Path,
    report_name: str = "clv_market_movement",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "generated_at_utc": "2026-04-10T12:00:00Z",
        "generated_by": "test",
        "source_runtime_root": str(runtime_root),
        "report_name": report_name,
        "report_version": "1.0",
        "orchestrator_run_id": f"shadow_artifacts_{prediction_date}_20260410T120000Z",
        "summary": {},
    }


def _write_core_boards(runtime_root: Path) -> None:
    for name in ("elite_board", "full_market_board", "sgp_board"):
        _write_text(
            runtime_root / "operator" / f"{name}_{PREDICTION_DATE}.csv",
            "player_name,market_type\n",
        )


def test_manifest_marks_missing_core_boards_as_fatal(tmp_path: Path) -> None:
    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        generated_at="2026-04-10T12:00:00Z",
    )

    for name in ("elite_board", "full_market_board", "sgp_board"):
        item = _artifact(manifest, name)
        assert item["exists"] is False
        assert item["severity"] == SEVERITY_FATAL
        assert "missing" in item["notes"]
    assert manifest["status"] == "fatal_missing"
    assert manifest["missing_by_severity"]["fatal"] == 3


def test_manifest_optional_artifacts_are_not_fatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    stat_only = _artifact(manifest, "stat_only_board")
    assert stat_only["exists"] is False
    assert stat_only["severity"] == SEVERITY_INFORMATIONAL
    assert manifest["status"] == "warning_missing"
    assert manifest["missing_by_severity"]["fatal"] == 0


def test_manifest_missing_kelly_stakes_is_warning_not_fatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    kelly = _artifact(manifest, "kelly_stakes")
    assert kelly["exists"] is False
    assert kelly["severity"] == SEVERITY_WARNING
    assert "absence is not fatal for no-bet slates" in kelly["notes"]
    assert manifest["status"] == "warning_missing"
    assert manifest["missing_by_severity"]["fatal"] == 0
    assert manifest["missing_by_severity"]["warning"] > 0


def test_manifest_missing_near_elite_review_is_shadow_only_not_fatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    near_elite = _artifact(manifest, "near_elite_review")
    assert near_elite["exists"] is False
    assert near_elite["severity"] == SEVERITY_SHADOW_ONLY
    assert "not an Elite, Kelly, SGP, or staking input" in near_elite["notes"]
    assert manifest["missing_by_severity"]["fatal"] == 0


def test_manifest_missing_clv_market_movement_is_shadow_only_not_fatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    clv_report = _artifact(manifest, "clv_market_movement_report")
    clv_json = _artifact(manifest, "clv_market_movement_diagnostics")
    assert clv_report["exists"] is False
    assert clv_json["exists"] is False
    assert clv_report["severity"] == SEVERITY_SHADOW_ONLY
    assert clv_json["severity"] == SEVERITY_SHADOW_ONLY
    assert "not an Elite, Kelly, SGP, or staking input" in clv_report["notes"]
    assert manifest["missing_by_severity"]["fatal"] == 0


def test_manifest_missing_calibration_bucket_report_is_shadow_only_not_fatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    report = _artifact(manifest, "calibration_bucket_report")
    diagnostics = _artifact(manifest, "calibration_bucket_report_diagnostics")
    assert report["exists"] is False
    assert diagnostics["exists"] is False
    assert report["severity"] == SEVERITY_SHADOW_ONLY
    assert diagnostics["severity"] == SEVERITY_SHADOW_ONLY
    assert "not an Elite, Kelly, SGP, final decision, or staking input" in report["notes"]
    assert manifest["missing_by_severity"]["fatal"] == 0


def test_manifest_missing_player_role_stability_is_shadow_only_not_fatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    report = _artifact(manifest, "player_role_stability_report")
    diagnostics = _artifact(manifest, "player_role_stability_report_diagnostics")
    assert report["exists"] is False
    assert diagnostics["exists"] is False
    assert report["severity"] == SEVERITY_SHADOW_ONLY
    assert diagnostics["severity"] == SEVERITY_SHADOW_ONLY
    assert "not an Elite, Kelly, SGP, final decision, or staking input" in report["notes"]
    assert manifest["missing_by_severity"]["fatal"] == 0


def test_manifest_missing_meta_label_promotion_is_shadow_only_not_fatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    report = _artifact(manifest, "meta_label_promotion_shadow_report")
    diagnostics = _artifact(manifest, "meta_label_promotion_shadow_diagnostics")
    csv_art = _artifact(manifest, "meta_label_promotion_shadow_csv")
    assert report["exists"] is False
    assert diagnostics["exists"] is False
    assert csv_art["exists"] is False
    assert report["severity"] == SEVERITY_SHADOW_ONLY
    assert diagnostics["severity"] == SEVERITY_SHADOW_ONLY
    assert csv_art["severity"] == SEVERITY_SHADOW_ONLY
    assert "not an Elite, Kelly, SGP, final decision, or staking input" in report["notes"]
    assert manifest["missing_by_severity"]["fatal"] == 0


def test_manifest_missing_feature_completeness_tracker_is_shadow_only_not_fatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    txt_art = _artifact(manifest, "feature_completeness_tracker_txt")
    json_art = _artifact(manifest, "feature_completeness_tracker_json")
    csv_art = _artifact(manifest, "feature_completeness_tracker_csv")
    assert txt_art["exists"] is False
    assert json_art["exists"] is False
    assert csv_art["exists"] is False
    assert txt_art["severity"] == SEVERITY_SHADOW_ONLY
    assert json_art["severity"] == SEVERITY_SHADOW_ONLY
    assert csv_art["severity"] == SEVERITY_SHADOW_ONLY
    assert "not a betting input" in txt_art["notes"]
    assert manifest["missing_by_severity"]["fatal"] == 0


def test_manifest_marks_current_date_phase4b_shadow_json_as_fresh(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)
    _write_json(
        runtime_root / "diagnostics" / f"clv_market_movement_{PREDICTION_DATE}.json",
        _shadow_payload(runtime_root=runtime_root),
    )

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    item = _artifact(manifest, "clv_market_movement_diagnostics")
    assert item["exists"] is True
    assert item["severity"] == SEVERITY_SHADOW_ONLY
    assert item["freshness_status"] == "fresh"
    assert item["prediction_date_match"] is True
    assert item["generated_at_utc"] == "2026-04-10T12:00:00Z"
    assert item["generated_by"] == "test"
    assert item["orchestrator_run_id"] == f"shadow_artifacts_{PREDICTION_DATE}_20260410T120000Z"


def test_manifest_marks_mismatched_phase4b_shadow_json_as_stale_date(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)
    _write_json(
        runtime_root / "diagnostics" / f"clv_market_movement_{PREDICTION_DATE}.json",
        _shadow_payload(prediction_date="2026-04-09", runtime_root=runtime_root),
    )

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    item = _artifact(manifest, "clv_market_movement_diagnostics")
    assert item["exists"] is True
    assert item["freshness_status"] == "stale_date"
    assert item["prediction_date_match"] is False
    assert manifest["missing_by_severity"]["fatal"] == 0


def test_manifest_marks_phase4b_shadow_json_missing_metadata_not_fresh(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)
    _write_json(
        runtime_root / "diagnostics" / f"clv_market_movement_{PREDICTION_DATE}.json",
        {"prediction_date": PREDICTION_DATE, "summary": {}},
    )

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    item = _artifact(manifest, "clv_market_movement_diagnostics")
    assert item["exists"] is True
    assert item["prediction_date_match"] is True
    assert item["freshness_status"] == "missing_metadata"
    assert "generated_at_utc" in item["missing_metadata_fields"]


def test_shadow_freshness_warnings_remain_nonfatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)
    _write_json(
        runtime_root / "diagnostics" / f"clv_market_movement_{PREDICTION_DATE}.json",
        _shadow_payload(prediction_date="2026-04-09", runtime_root=runtime_root),
    )

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    assert _artifact(manifest, "clv_market_movement_diagnostics")["freshness_status"] == "stale_date"
    assert manifest["shadow_freshness_counts"]["stale_date"] == 1
    assert manifest["missing_by_severity"]["fatal"] == 0
    assert manifest["status"] != "fatal_missing"


def test_manifest_counts_csv_rows(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_text(
        runtime_root / "operator" / f"elite_board_{PREDICTION_DATE}.csv",
        "player_name,market_type\nA,player_points\nB,player_points\n",
    )

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    elite = _artifact(manifest, "elite_board")
    assert elite["exists"] is True
    assert elite["row_count"] == 2
    assert elite["size_bytes"] is not None


def test_write_artifact_manifest_outputs_writes_json_and_text(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    text_path, json_path, manifest = write_artifact_manifest_outputs(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    assert text_path == runtime_root / "operator" / f"artifact_manifest_{PREDICTION_DATE}.txt"
    assert json_path == runtime_root / "diagnostics" / f"artifact_manifest_{PREDICTION_DATE}.json"
    assert text_path.exists()
    assert json_path.exists()
    assert "Artifact Manifest - 2026-04-10" in text_path.read_text(encoding="utf-8")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["prediction_date"] == PREDICTION_DATE
    assert payload["generated_at"] == "2026-04-10T12:00:00Z"
    assert payload["artifact_count"] == manifest["artifact_count"]


# ---------------------------------------------------------------------------
# Phase 6A Learning Artifacts — optional/reporting-only manifest entries
# ---------------------------------------------------------------------------

def test_manifest_learning_brain_report_is_shadow_only_not_fatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    txt = _artifact(manifest, "learning_brain_report_txt")
    jsn = _artifact(manifest, "learning_brain_report_json")
    assert txt["exists"] is False
    assert jsn["exists"] is False
    assert txt["severity"] == SEVERITY_SHADOW_ONLY
    assert jsn["severity"] == SEVERITY_SHADOW_ONLY
    assert "not an Elite, Kelly, final decision, or staking input" in txt["notes"]
    assert manifest["missing_by_severity"]["fatal"] == 0


def test_manifest_shadow_rule_proposals_is_shadow_only_not_fatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    txt = _artifact(manifest, "shadow_rule_proposals_txt")
    jsn = _artifact(manifest, "shadow_rule_proposals_json")
    assert txt["exists"] is False
    assert jsn["exists"] is False
    assert txt["severity"] == SEVERITY_SHADOW_ONLY
    assert jsn["severity"] == SEVERITY_SHADOW_ONLY
    assert "not an Elite, Kelly, final decision, or staking input" in txt["notes"]
    assert manifest["missing_by_severity"]["fatal"] == 0


def test_manifest_learning_integration_snapshot_is_shadow_only_not_fatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    txt = _artifact(manifest, "learning_integration_snapshot_txt")
    jsn = _artifact(manifest, "learning_integration_snapshot_json")
    assert txt["exists"] is False
    assert jsn["exists"] is False
    assert txt["severity"] == SEVERITY_SHADOW_ONLY
    assert jsn["severity"] == SEVERITY_SHADOW_ONLY
    assert "not an Elite, Kelly, final decision, or staking input" in txt["notes"]
    assert manifest["missing_by_severity"]["fatal"] == 0


def test_manifest_learning_artifacts_summary_is_shadow_only_not_fatal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    txt = _artifact(manifest, "learning_artifacts_summary_txt")
    jsn = _artifact(manifest, "learning_artifacts_summary_json")
    assert txt["exists"] is False
    assert jsn["exists"] is False
    assert txt["severity"] == SEVERITY_SHADOW_ONLY
    assert jsn["severity"] == SEVERITY_SHADOW_ONLY
    assert "not an Elite, Kelly, final decision, or staking input" in txt["notes"]
    assert manifest["missing_by_severity"]["fatal"] == 0


def test_manifest_learning_artifacts_are_not_betting_required(tmp_path: Path) -> None:
    """All 8 Phase 6A learning artifact specs must be shadow-only with zero fatal impact."""
    runtime_root = tmp_path / "runtime"
    _write_core_boards(runtime_root)

    manifest = build_artifact_manifest(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        generated_at="2026-04-10T12:00:00Z",
    )

    learning_names = [
        "learning_brain_report_txt",
        "learning_brain_report_json",
        "shadow_rule_proposals_txt",
        "shadow_rule_proposals_json",
        "learning_integration_snapshot_txt",
        "learning_integration_snapshot_json",
        "learning_artifacts_summary_txt",
        "learning_artifacts_summary_json",
    ]
    for name in learning_names:
        item = _artifact(manifest, name)
        assert item["severity"] == SEVERITY_SHADOW_ONLY, (
            f"{name} must be SEVERITY_SHADOW_ONLY, got {item['severity']}"
        )
        assert item["exists"] is False  # not written in this test
        assert item["category"] == "learning_artifacts_reporting"

    # All 8 are shadow_only — fatal_missing must remain 0
    assert manifest["missing_by_severity"]["fatal"] == 0

