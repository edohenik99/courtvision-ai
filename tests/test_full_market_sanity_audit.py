from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.audit_full_market_sanity import (
    STATUS_FAIL_DUPLICATES,
    STATUS_FAIL_MISSING_FULL_MARKET,
    STATUS_FAIL_UNSUPPORTED_MARKET,
    STATUS_PASS,
    STATUS_PASS_NO_SLATE,
    STATUS_PASS_WITH_WARNINGS,
    build_full_market_sanity_audit,
    write_full_market_sanity_audit,
)


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_operator_card(path: Path, *, final_decision: str = "NO BET", games_count: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "COURTVISION DAILY CARD",
                f"final_decision: {final_decision}",
                "",
                "Slate Summary",
                f"- games count: {games_count}",
                "",
            ]
        ),
        encoding="utf-8",
    )


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
    quality_score: float | str = 60.0,
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


def _issue_codes(payload: dict) -> set[str]:
    return {issue["code"] for issue in payload["issues"]}


def test_clean_full_market_board_passes(tmp_path: Path) -> None:
    prediction_date = "2026-05-10"
    runtime_root = tmp_path / "runtime"
    rows = [
        _candidate(prediction_date, player_name="Points Over", market_type="player_points", selection="over", line=12.5),
        _candidate(prediction_date, player_name="Boards Under", market_type="player_rebounds", selection="under", line=7.5),
    ]
    _seed_full_market(runtime_root, prediction_date, rows, elite_rows=[rows[0]])

    payload = build_full_market_sanity_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS
    assert payload["total_rows"] == 2
    assert payload["elite_rows"] == 1
    assert payload["warning_count"] == 0
    assert payload["failure_count"] == 0


def test_missing_full_market_board_fails_and_writes_outputs(tmp_path: Path) -> None:
    prediction_date = "2026-05-11"
    runtime_root = tmp_path / "runtime"

    text_path, json_path, csv_path, payload = write_full_market_sanity_audit(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    assert payload["status"] == STATUS_FAIL_MISSING_FULL_MARKET
    assert "missing_full_market_board" in _issue_codes(payload)
    assert text_path.exists()
    assert json_path.exists()
    assert csv_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == STATUS_FAIL_MISSING_FULL_MARKET


def test_empty_full_market_board_warns_cleanly(tmp_path: Path) -> None:
    prediction_date = "2026-05-12"
    runtime_root = tmp_path / "runtime"
    _seed_full_market(runtime_root, prediction_date, [])

    payload = build_full_market_sanity_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS_WITH_WARNINGS
    assert "empty_full_market_board" in _issue_codes(payload)
    assert payload["total_rows"] == 0


def test_empty_full_market_board_with_no_slate_context_passes_without_warning(tmp_path: Path) -> None:
    prediction_date = "2026-05-12"
    runtime_root = tmp_path / "runtime"
    _seed_full_market(runtime_root, prediction_date, [])
    _write_operator_card(runtime_root / "operator" / f"operator_card_{prediction_date}.txt")

    payload = build_full_market_sanity_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS_NO_SLATE
    assert payload["warning_count"] == 0
    assert payload["failure_count"] == 0
    assert payload["no_slate_context"] is True
    assert "empty_full_market_board" not in _issue_codes(payload)


def test_duplicate_rows_fail(tmp_path: Path) -> None:
    prediction_date = "2026-05-13"
    runtime_root = tmp_path / "runtime"
    row = _candidate(prediction_date, player_name="Duplicate Player", market_type="player_points", selection="over", line=14.5)
    _seed_full_market(runtime_root, prediction_date, [row, dict(row)])

    payload = build_full_market_sanity_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_FAIL_DUPLICATES
    assert "duplicate_candidate_rows" in _issue_codes(payload)


def test_unsupported_blocks_and_steals_markets_fail(tmp_path: Path) -> None:
    prediction_date = "2026-05-14"
    runtime_root = tmp_path / "runtime"
    _seed_full_market(
        runtime_root,
        prediction_date,
        [
            _candidate(prediction_date, player_name="Blocks Player", market_type="player_blocks", line=1.5),
            _candidate(prediction_date, player_name="Steals Player", market_type="player_steals", line=1.5),
        ],
    )

    payload = build_full_market_sanity_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_FAIL_UNSUPPORTED_MARKET
    assert "unsupported_market_type" in _issue_codes(payload)
    assert payload["failure_count"] == 1


def test_suspicious_assists_line_warns(tmp_path: Path) -> None:
    prediction_date = "2026-05-15"
    runtime_root = tmp_path / "runtime"
    _seed_full_market(
        runtime_root,
        prediction_date,
        [_candidate(prediction_date, market_type="player_assists", line=24.5)],
    )

    payload = build_full_market_sanity_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS_WITH_WARNINGS
    assert "suspicious_line" in _issue_codes(payload)


def test_non_finite_edge_or_projection_warns(tmp_path: Path) -> None:
    prediction_date = "2026-05-16"
    runtime_root = tmp_path / "runtime"
    _seed_full_market(
        runtime_root,
        prediction_date,
        [_candidate(prediction_date, model_projection="inf", edge="inf")],
    )

    payload = build_full_market_sanity_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS_WITH_WARNINGS
    assert "non_finite_projection_values" in _issue_codes(payload)
    assert "non_finite_edge_values" in _issue_codes(payload)


def test_elite_row_missing_from_full_market_warns(tmp_path: Path) -> None:
    prediction_date = "2026-05-17"
    runtime_root = tmp_path / "runtime"
    full_row = _candidate(prediction_date, player_name="Full Player", market_type="player_points", selection="over", line=12.5)
    elite_row = _candidate(prediction_date, player_name="Elite Missing", market_type="player_points", selection="over", line=12.5)
    _seed_full_market(runtime_root, prediction_date, [full_row], elite_rows=[elite_row])

    payload = build_full_market_sanity_audit(prediction_date=prediction_date, runtime_root=runtime_root)

    assert payload["status"] == STATUS_PASS_WITH_WARNINGS
    assert "elite_rows_missing_from_full_market" in _issue_codes(payload)


def test_output_txt_json_csv_are_written(tmp_path: Path) -> None:
    prediction_date = "2026-05-18"
    runtime_root = tmp_path / "runtime"
    rows = [_candidate(prediction_date)]
    _seed_full_market(runtime_root, prediction_date, rows, elite_rows=rows)
    board_path = runtime_root / "operator" / f"full_market_board_{prediction_date}.csv"
    before = board_path.read_bytes()

    text_path, json_path, csv_path, payload = write_full_market_sanity_audit(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    assert payload["status"] == STATUS_PASS
    assert text_path.exists()
    assert json_path.exists()
    assert csv_path.exists()
    assert "Full-Market Candidate Sanity Audit" in text_path.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["read_only"] is True
    assert board_path.read_bytes() == before
