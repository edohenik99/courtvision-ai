import json
from pathlib import Path

from courtvision.reporting.artifact_manifest import (
    SEVERITY_FATAL,
    SEVERITY_INFORMATIONAL,
    build_artifact_manifest,
    write_artifact_manifest_outputs,
)


PREDICTION_DATE = "2026-04-10"


def _artifact(manifest: dict, name: str) -> dict:
    return next(item for item in manifest["artifacts"] if item["name"] == name)


def _write_text(path: Path, text: str = "ok\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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
    assert manifest["missing_by_severity"]["fatal"] == 0


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
