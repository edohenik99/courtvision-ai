from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.audit_candidate_quality_drift import (
    STATUS_FAIL_MISSING_FULL_MARKET,
    STATUS_FAIL_UNSUPPORTED_ELITE_MARKET,
    STATUS_PASS,
    STATUS_PASS_NO_SLATE,
    STATUS_PASS_WITH_WARNINGS,
    build_candidate_quality_drift_audit,
    write_candidate_quality_drift_audit,
)


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _candidate(
    prediction_date: str,
    *,
    player_name: str = "Fixture Player",
    market_type: str = "player_points",
    selection: str = "over",
    line: float | str = 12.5,
    model_projection: float | str = 15.0,
    edge: float | str = 2.5,
    confidence: float | str = 0.72,
    quality_score: float | str = 0.80,
    team_abbr: str = "BOS",
    opponent: str = "NYK",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "entity_name": player_name,
        "team_abbr": team_abbr,
        "opponent": opponent,
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "sportsbook_line": line,
        "model_projection": model_projection,
        "projection": model_projection,
        "edge": edge,
        "side_edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "selection_score": quality_score,
    }


def _seed_full_market(
    runtime_root: Path,
    prediction_date: str,
    rows: list[dict],
    *,
    elite_rows: list[dict] | None = None,
) -> None:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    columns = list(_candidate(prediction_date).keys())
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", rows, columns=columns)
    if elite_rows is not None:
        _write_csv(operator / f"elite_board_{prediction_date}.csv", elite_rows, columns=columns)
    _write_json(diagnostics / f"board_diagnostics_{prediction_date}.json", {"board_counts": {"qualified_pool": len(rows)}})


def _clean_rows(prediction_date: str) -> list[dict]:
    return [
        _candidate(prediction_date, player_name="Points Over", market_type="player_points", selection="over"),
        _candidate(prediction_date, player_name="Points Under", market_type="player_points", selection="under"),
        _candidate(prediction_date, player_name="Boards Over", market_type="player_rebounds", selection="over", line=7.5),
        _candidate(prediction_date, player_name="Boards Under", market_type="player_rebounds", selection="under", line=7.5),
    ]


def _issue_codes(payload: dict) -> set[str]:
    return {issue["code"] for issue in payload["issues"]}


def test_clean_board_passes(tmp_path: Path) -> None:
    prediction_date = "2026-05-10"
    runtime_root = tmp_path / "runtime"
    rows = _clean_rows(prediction_date)
    _seed_full_market(runtime_root, prediction_date, rows, elite_rows=rows[:2])

    payload = build_candidate_quality_drift_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS
    assert payload["total_rows"] == 4
    assert payload["elite_rows"] == 2
    assert payload["market_counts"] == {"player_points": 2, "player_rebounds": 2}
    assert payload["selection_counts"] == {"over": 2, "under": 2}
    assert payload["warning_count"] == 0
    assert payload["failure_count"] == 0
    assert payload["suspicious_rows"]["very_large_absolute_edge"] == []


def test_missing_board_fails_and_writes_outputs(tmp_path: Path) -> None:
    prediction_date = "2026-05-11"
    runtime_root = tmp_path / "runtime"

    text_path, json_path, csv_path, payload = write_candidate_quality_drift_audit(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    assert payload["status"] == STATUS_FAIL_MISSING_FULL_MARKET
    assert "missing_full_market_board" in _issue_codes(payload)
    assert text_path.exists()
    assert json_path.exists()
    assert csv_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == STATUS_FAIL_MISSING_FULL_MARKET


def test_no_slate_empty_board_passes_with_no_slate_context(tmp_path: Path) -> None:
    prediction_date = "2026-05-12"
    runtime_root = tmp_path / "runtime"
    _seed_full_market(runtime_root, prediction_date, [])
    _write_json(
        runtime_root / "diagnostics" / f"full_market_sanity_audit_{prediction_date}.json",
        {"status": "PASS_NO_SLATE", "no_slate_context": True},
    )

    payload = build_candidate_quality_drift_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS_NO_SLATE
    assert payload["total_rows"] == 0
    assert payload["warning_count"] == 0
    assert payload["failure_count"] == 0
    assert payload["no_slate_context"] is True


def test_empty_board_without_no_slate_context_warns_cleanly(tmp_path: Path) -> None:
    prediction_date = "2026-05-12"
    runtime_root = tmp_path / "runtime"
    _seed_full_market(runtime_root, prediction_date, [])

    payload = build_candidate_quality_drift_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS_WITH_WARNINGS
    assert payload["total_rows"] == 0
    assert "empty_full_market_board" in _issue_codes(payload)


def test_side_imbalance_warns(tmp_path: Path) -> None:
    prediction_date = "2026-05-13"
    runtime_root = tmp_path / "runtime"
    rows = [
        _candidate(prediction_date, player_name=f"Over {idx}", market_type="player_points", selection="over")
        for idx in range(9)
    ]
    rows.append(_candidate(prediction_date, player_name="Under 1", market_type="player_points", selection="under"))
    _seed_full_market(runtime_root, prediction_date, rows)

    payload = build_candidate_quality_drift_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS_WITH_WARNINGS
    assert "side_imbalance_by_market" in _issue_codes(payload)
    assert payload["side_imbalance_by_market_type"][0]["top_side_share"] == 0.9


def test_huge_edge_warns(tmp_path: Path) -> None:
    prediction_date = "2026-05-14"
    runtime_root = tmp_path / "runtime"
    rows = [
        _candidate(
            prediction_date,
            player_name="Assists Huge Edge",
            market_type="player_assists",
            selection="over",
            line=4.5,
            model_projection=13.7,
            edge=9.2,
        ),
        _candidate(
            prediction_date,
            player_name="Assists Balanced Side",
            market_type="player_assists",
            selection="under",
            line=4.5,
            model_projection=6.0,
            edge=1.5,
        ),
    ]
    _seed_full_market(runtime_root, prediction_date, rows)

    payload = build_candidate_quality_drift_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS_WITH_WARNINGS
    assert "very_large_absolute_edge" in _issue_codes(payload)
    assert payload["suspicious_rows"]["very_large_absolute_edge"][0]["player_name"] == "Assists Huge Edge"


def test_high_confidence_low_quality_warns(tmp_path: Path) -> None:
    prediction_date = "2026-05-15"
    runtime_root = tmp_path / "runtime"
    rows = [
        _candidate(
            prediction_date,
            player_name="High Confidence Low Quality",
            market_type="player_points",
            selection="over",
            confidence=0.80,
            quality_score=0.50,
        ),
        _candidate(prediction_date, player_name="Balanced Side", market_type="player_points", selection="under"),
    ]
    _seed_full_market(runtime_root, prediction_date, rows)

    payload = build_candidate_quality_drift_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS_WITH_WARNINGS
    assert "high_confidence_low_quality" in _issue_codes(payload)
    assert payload["suspicious_rows"]["high_confidence_low_quality"][0]["quality_score_value"] == 0.5


def test_missing_metric_fields_warn(tmp_path: Path) -> None:
    prediction_date = "2026-05-16"
    runtime_root = tmp_path / "runtime"
    rows = [
        _candidate(
            prediction_date,
            player_name="Missing Metrics",
            market_type="player_points",
            selection="over",
            model_projection="",
            edge="",
            confidence="",
            quality_score="",
        ),
        _candidate(prediction_date, player_name="Balanced Side", market_type="player_points", selection="under"),
    ]
    _seed_full_market(runtime_root, prediction_date, rows)

    payload = build_candidate_quality_drift_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS_WITH_WARNINGS
    assert {"missing_projection_values", "missing_edge_values", "missing_confidence_values", "missing_quality_score_values"} <= _issue_codes(payload)
    assert payload["suspicious_rows"]["missing_fields"][0]["missing_fields"] == [
        "projection",
        "edge",
        "confidence",
        "quality_score",
    ]


def test_elite_unsupported_market_fails(tmp_path: Path) -> None:
    prediction_date = "2026-05-17"
    runtime_root = tmp_path / "runtime"
    rows = _clean_rows(prediction_date)
    elite_rows = [
        _candidate(prediction_date, player_name="Unsupported Elite", market_type="player_blocks", selection="over", line=1.5)
    ]
    _seed_full_market(runtime_root, prediction_date, rows, elite_rows=elite_rows)

    payload = build_candidate_quality_drift_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_FAIL_UNSUPPORTED_ELITE_MARKET
    assert "unsupported_elite_market_type" in _issue_codes(payload)
    assert payload["failure_count"] == 1


def test_output_txt_json_csv_are_written_without_mutating_board(tmp_path: Path) -> None:
    prediction_date = "2026-05-18"
    runtime_root = tmp_path / "runtime"
    rows = [
        _candidate(prediction_date, player_name="Points Over", market_type="player_points", selection="over"),
        _candidate(prediction_date, player_name="Points Under", market_type="player_points", selection="under"),
    ]
    _seed_full_market(runtime_root, prediction_date, rows, elite_rows=rows[:1])
    board_path = runtime_root / "operator" / f"full_market_board_{prediction_date}.csv"
    before = board_path.read_bytes()

    text_path, json_path, csv_path, payload = write_candidate_quality_drift_audit(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    assert payload["status"] == STATUS_PASS
    assert text_path.exists()
    assert json_path.exists()
    assert csv_path.exists()
    assert "Candidate Quality Drift Audit" in text_path.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["read_only"] is True
    assert board_path.read_bytes() == before
