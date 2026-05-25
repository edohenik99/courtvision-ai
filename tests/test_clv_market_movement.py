from __future__ import annotations

from pathlib import Path

import pandas as pd

from courtvision.market_intelligence.market_snapshots import market_snapshot_key
from courtvision.reporting.clv_market_movement import (
    DIAGNOSTIC_ONLY_NOTE,
    build_clv_market_movement_report,
    write_clv_market_movement_report,
)
from scripts.history_tracking import persist_market_shadow_history


DATE = "2026-05-24"


def _entry_row(
    *,
    player_name: str = "Fixture Player",
    player_id: str = "player-1",
    selection: str = "over",
    line: float = 20.5,
    odds: int = -110,
) -> dict:
    return {
        "prediction_date": DATE,
        "game_id": "game-1",
        "player_id": player_id,
        "player_name": player_name,
        "team_abbr": "BOS",
        "opponent": "NYK",
        "market_type": "player_points",
        "selection": selection,
        "line": line,
        "sportsbook_line": line,
        "odds": odds,
        "model_projection": line + 2,
        "edge": 2.0,
        "confidence": 0.72,
        "quality_score": 80.0,
    }


def _close_row(entry: dict, *, opening: float, closing: float, close_odds: int = -105) -> dict:
    return {
        **{key: entry[key] for key in (
            "prediction_date",
            "game_id",
            "player_id",
            "player_name",
            "team_abbr",
            "opponent",
            "market_type",
            "selection",
        )},
        "opening_line_observed": opening,
        "closing_line_observed": closing,
        "closing_odds_observed": close_odds,
        "close_source": "fixture_close",
    }


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def test_market_snapshot_key_is_deterministic() -> None:
    row_a = {
        "prediction_date": "2026-05-24",
        "game_id": "game-1",
        "player_id": "player-1",
        "player_name": "Fixture Player",
        "team": "bos",
        "opponent": "nyk",
        "market_type": "Player Points",
        "selection": "OVER",
    }
    row_b = {
        "prediction_date": " 2026-05-24 ",
        "game_id": "game-1",
        "player_id": "player-1",
        "player_name": " Fixture   Player ",
        "team_abbr": "BOS",
        "opponent": "NYK",
        "market_type": "player_points",
        "selection": "over",
    }

    assert market_snapshot_key(row_a) == market_snapshot_key(row_b)
    assert market_snapshot_key(row_a) != market_snapshot_key({**row_b, "selection": "under"})


def test_over_clv_formula() -> None:
    entry = _entry_row(selection="over", line=20.5)
    close = _close_row(entry, opening=20.0, closing=22.0)

    payload = build_clv_market_movement_report(
        pd.DataFrame([entry]),
        prediction_date=DATE,
        close_snapshots_df=pd.DataFrame([close]),
    )

    row = payload["rows"][0]
    assert row["clv_line_points"] == 1.5
    assert row["line_move_points"] == 2.0
    assert row["movement_toward_pick"] is True
    assert row["clv_grade"] == "positive"


def test_under_clv_formula() -> None:
    entry = _entry_row(selection="under", line=8.5)
    close = _close_row(entry, opening=8.0, closing=7.5)

    payload = build_clv_market_movement_report(
        pd.DataFrame([entry]),
        prediction_date=DATE,
        close_snapshots_df=pd.DataFrame([close]),
    )

    row = payload["rows"][0]
    assert row["clv_line_points"] == 1.0
    assert row["line_move_points"] == -0.5
    assert row["movement_toward_pick"] is True
    assert row["clv_grade"] == "positive"


def test_missing_close_line_is_missing_coverage_not_failure() -> None:
    payload = build_clv_market_movement_report(
        pd.DataFrame([_entry_row()]),
        prediction_date=DATE,
        close_snapshots_df=pd.DataFrame(),
    )

    row = payload["rows"][0]
    assert row["close_coverage_status"] == "missing"
    assert row["closing_line_observed"] is None
    assert row["clv_line_points"] is None
    assert payload["summary"]["missing_close_line_count"] == 1


def test_report_only_outputs_do_not_alter_elite_or_kelly_rows(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    entry = _entry_row()
    elite_row = {**entry, "player_name": "Elite Sentinel"}
    kelly_row = {**entry, "player_name": "Kelly Sentinel", "eligible": True, "stake_amount": 10.0}

    _write_csv(operator / f"full_market_board_{DATE}.csv", [entry])
    _write_csv(operator / f"elite_board_{DATE}.csv", [elite_row])
    _write_csv(operator / f"kelly_stakes_{DATE}.csv", [kelly_row])
    elite_path = operator / f"elite_board_{DATE}.csv"
    kelly_path = operator / f"kelly_stakes_{DATE}.csv"
    elite_before = elite_path.read_bytes()
    kelly_before = kelly_path.read_bytes()

    json_path, txt_path, payload = write_clv_market_movement_report(
        prediction_date=DATE,
        runtime_root=runtime_root,
        close_snapshots_df=pd.DataFrame([_close_row(entry, opening=20.0, closing=21.0)]),
    )

    assert json_path.exists()
    assert txt_path.exists()
    assert DIAGNOSTIC_ONLY_NOTE in txt_path.read_text(encoding="utf-8")
    assert payload["summary"]["close_coverage_count"] == 1
    assert elite_path.read_bytes() == elite_before
    assert kelly_path.read_bytes() == kelly_before


def test_reruns_preserve_grading_history_and_do_not_overwrite_closed_slate_boards(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    entry = _entry_row()
    history_row = {
        **entry,
        "market_snapshot_key": market_snapshot_key(entry),
        "team": "BOS",
        "team_abbr": "BOS",
        "line": 20.5,
        "entry_line": 20.5,
        "entry_odds": -110,
        "opening_line_observed": 20.0,
        "closing_line_observed": 21.0,
        "close_source": "fixture_close",
        "close_coverage_status": "observed",
        "line_move_points": 1.0,
        "movement_toward_pick": True,
        "clv_line_points": 0.5,
        "clv_odds_delta": 5,
        "clv_grade": "positive",
        "clv_confidence": 1.0,
        "result_status": "hit",
        "actual_value": 23.0,
        "hit": True,
        "miss": False,
        "push": False,
        "shadow_roi": 0.909091,
    }
    history_root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([history_row]).to_csv(history_root / "market_shadow_history.csv", index=False)

    _write_csv(operator / f"full_market_board_{DATE}.csv", [entry])
    _write_csv(operator / f"elite_board_{DATE}.csv", [{**entry, "player_name": "Elite Sentinel"}])
    _write_csv(operator / f"kelly_stakes_{DATE}.csv", [{**entry, "player_name": "Kelly Sentinel"}])
    board_before = (operator / f"full_market_board_{DATE}.csv").read_bytes()
    elite_before = (operator / f"elite_board_{DATE}.csv").read_bytes()
    kelly_before = (operator / f"kelly_stakes_{DATE}.csv").read_bytes()

    persist_market_shadow_history(
        prediction_date=DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    shadow = pd.read_csv(history_root / "market_shadow_history.csv", keep_default_na=False)
    row = shadow.iloc[0]
    assert row["result_status"] == "hit"
    assert float(row["actual_value"]) == 23.0
    assert row["close_coverage_status"] == "observed"
    assert float(row["clv_line_points"]) == 0.5
    assert (operator / f"full_market_board_{DATE}.csv").read_bytes() == board_before
    assert (operator / f"elite_board_{DATE}.csv").read_bytes() == elite_before
    assert (operator / f"kelly_stakes_{DATE}.csv").read_bytes() == kelly_before
