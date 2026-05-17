from __future__ import annotations

from pathlib import Path

import pytest

from scripts import audit_candidate_quality_drift as candidate
from scripts import audit_full_market_sanity as full_sanity
from scripts import validate_historical_cockpit as cockpit


def _audit_paths(tmp_path: Path, name: str) -> dict[str, str]:
    return {
        "text": str(tmp_path / "runtime" / "operator" / f"{name}.txt"),
        "json": str(tmp_path / "runtime" / "diagnostics" / f"{name}.json"),
        "csv": str(tmp_path / "runtime" / "diagnostics" / f"{name}.csv"),
    }


def test_candidate_quality_drift_guard_blocks_existing_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _audit_paths(tmp_path, "candidate_quality_drift_audit_2026-05-06")
    text_path = Path(paths["text"])
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("existing\n", encoding="utf-8")

    def fake_build_candidate_quality_drift_audit(**_kwargs: object) -> dict[str, object]:
        return {"artifact_paths": paths}

    monkeypatch.setattr(candidate, "build_candidate_quality_drift_audit", fake_build_candidate_quality_drift_audit)

    with pytest.raises(RuntimeError, match=r"\[ARTIFACT_OVERWRITE_GUARD\]"):
        candidate.write_candidate_quality_drift_audit(
            prediction_date="2026-05-06",
            runtime_root=tmp_path / "runtime",
        )

    assert text_path.read_text(encoding="utf-8") == "existing\n"
    assert not Path(paths["json"]).exists()
    assert not Path(paths["csv"]).exists()


def test_candidate_quality_drift_force_allows_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _audit_paths(tmp_path, "candidate_quality_drift_audit_2026-05-06")
    text_path = Path(paths["text"])
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("existing\n", encoding="utf-8")

    monkeypatch.setattr(candidate, "build_candidate_quality_drift_audit", lambda **_kwargs: {"artifact_paths": paths})
    monkeypatch.setattr(candidate, "render_candidate_quality_drift_text", lambda _payload: "new candidate text")
    monkeypatch.setattr(candidate, "_issue_csv_rows", lambda _payload: [])

    written_text, written_json, written_csv, payload = candidate.write_candidate_quality_drift_audit(
        prediction_date="2026-05-06",
        runtime_root=tmp_path / "runtime",
        force=True,
    )

    assert payload["artifact_paths"] == paths
    assert written_text == text_path
    assert written_json == Path(paths["json"])
    assert written_csv == Path(paths["csv"])
    assert text_path.read_text(encoding="utf-8") == "new candidate text"
    assert Path(paths["json"]).exists()
    assert Path(paths["csv"]).exists()


def test_candidate_quality_drift_cli_passes_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_write_candidate_quality_drift_audit(**kwargs: object):
        captured.update(kwargs)
        return (
            tmp_path / "candidate.txt",
            tmp_path / "candidate.json",
            tmp_path / "candidate.csv",
            {"status": "PASS", "total_rows": 0, "elite_rows": 0, "warning_count": 0, "failure_count": 0},
        )

    monkeypatch.setattr(candidate, "write_candidate_quality_drift_audit", fake_write_candidate_quality_drift_audit)

    assert candidate.main(["--prediction-date", "2026-05-06", "--runtime-root", str(tmp_path), "--force"]) == 0
    assert captured["force"] is True


def test_full_market_sanity_guard_blocks_existing_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _audit_paths(tmp_path, "full_market_sanity_audit_2026-05-06")
    json_path = Path(paths["json"])
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text('{"existing": true}\n', encoding="utf-8")

    monkeypatch.setattr(full_sanity, "build_full_market_sanity_audit", lambda **_kwargs: {"artifact_paths": paths})

    with pytest.raises(RuntimeError, match=r"\[ARTIFACT_OVERWRITE_GUARD\]"):
        full_sanity.write_full_market_sanity_audit(
            prediction_date="2026-05-06",
            runtime_root=tmp_path / "runtime",
        )

    assert not Path(paths["text"]).exists()
    assert json_path.read_text(encoding="utf-8") == '{"existing": true}\n'
    assert not Path(paths["csv"]).exists()


def test_full_market_sanity_force_allows_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _audit_paths(tmp_path, "full_market_sanity_audit_2026-05-06")
    json_path = Path(paths["json"])
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text('{"existing": true}\n', encoding="utf-8")

    monkeypatch.setattr(full_sanity, "build_full_market_sanity_audit", lambda **_kwargs: {"artifact_paths": paths})
    monkeypatch.setattr(full_sanity, "render_full_market_sanity_text", lambda _payload: "new sanity text")
    monkeypatch.setattr(full_sanity, "_issue_csv_rows", lambda _payload: [])

    written_text, written_json, written_csv, payload = full_sanity.write_full_market_sanity_audit(
        prediction_date="2026-05-06",
        runtime_root=tmp_path / "runtime",
        force=True,
    )

    assert payload["artifact_paths"] == paths
    assert written_text == Path(paths["text"])
    assert written_json == json_path
    assert written_csv == Path(paths["csv"])
    assert Path(paths["text"]).read_text(encoding="utf-8") == "new sanity text"
    assert json_path.exists()
    assert Path(paths["csv"]).exists()


def test_full_market_sanity_cli_passes_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_write_full_market_sanity_audit(**kwargs: object):
        captured.update(kwargs)
        return (
            tmp_path / "sanity.txt",
            tmp_path / "sanity.json",
            tmp_path / "sanity.csv",
            {"status": "PASS", "total_rows": 0, "elite_rows": 0, "warning_count": 0, "failure_count": 0},
        )

    monkeypatch.setattr(full_sanity, "write_full_market_sanity_audit", fake_write_full_market_sanity_audit)

    assert full_sanity.main(["--prediction-date", "2026-05-06", "--runtime-root", str(tmp_path), "--force"]) == 0
    assert captured["force"] is True


def test_historical_cockpit_guard_blocks_existing_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    paths = cockpit._output_paths(runtime_root, "2026-05-01", "2026-05-06")
    csv_path = paths["csv"]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("existing\n", encoding="utf-8")

    monkeypatch.setattr(
        cockpit,
        "build_historical_cockpit_validation",
        lambda **_kwargs: {"dates": [], "summary": {"total_dates_checked": 0}},
    )

    with pytest.raises(RuntimeError, match=r"\[ARTIFACT_OVERWRITE_GUARD\]"):
        cockpit.write_historical_cockpit_validation(
            start_date="2026-05-01",
            end_date="2026-05-06",
            runtime_root=runtime_root,
        )

    assert not paths["text"].exists()
    assert not paths["json"].exists()
    assert csv_path.read_text(encoding="utf-8") == "existing\n"


def test_historical_cockpit_force_allows_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    paths = cockpit._output_paths(runtime_root, "2026-05-01", "2026-05-06")
    csv_path = paths["csv"]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("existing\n", encoding="utf-8")

    payload = {
        "dates": [],
        "summary": {
            "total_dates_checked": 0,
            "pass_count": 0,
            "warning_count": 0,
            "fail_count": 0,
            "missing_artifact_count": 0,
        },
    }

    monkeypatch.setattr(cockpit, "build_historical_cockpit_validation", lambda **_kwargs: payload)
    monkeypatch.setattr(cockpit, "render_historical_cockpit_validation_text", lambda _payload: "new cockpit text")
    monkeypatch.setattr(cockpit, "_csv_rows", lambda _rows: [])

    written_text, written_json, written_csv, written_payload = cockpit.write_historical_cockpit_validation(
        start_date="2026-05-01",
        end_date="2026-05-06",
        runtime_root=runtime_root,
        force=True,
    )

    assert written_payload is payload
    assert written_text == paths["text"]
    assert written_json == paths["json"]
    assert written_csv == csv_path
    assert paths["text"].read_text(encoding="utf-8") == "new cockpit text"
    assert paths["json"].exists()
    assert csv_path.exists()


def test_historical_cockpit_cli_passes_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    payload = {
        "summary": {
            "total_dates_checked": 0,
            "pass_count": 0,
            "warning_count": 0,
            "fail_count": 0,
            "missing_artifact_count": 0,
        },
        "dates": [],
    }

    def fake_write_historical_cockpit_validation(**kwargs: object):
        captured.update(kwargs)
        return tmp_path / "cockpit.txt", tmp_path / "cockpit.json", tmp_path / "cockpit.csv", payload

    monkeypatch.setattr(cockpit, "write_historical_cockpit_validation", fake_write_historical_cockpit_validation)

    assert cockpit.main(
        [
            "--start-date",
            "2026-05-01",
            "--end-date",
            "2026-05-06",
            "--runtime-root",
            str(tmp_path),
            "--force",
        ]
    ) == 0
    assert captured["force"] is True
