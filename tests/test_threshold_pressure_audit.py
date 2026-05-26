from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.threshold_pressure_audit import (
    DISCLAIMER,
    american_odds_breakeven_probability,
    build_threshold_pressure_audit,
    render_threshold_pressure_audit,
    unit_profit_for_result,
    write_threshold_pressure_audit,
)


PREDICTION_DATE = "2026-05-24"


def _history_row(
    *,
    player_name: str,
    result_status: str,
    odds: int = 100,
    rejection_reason: str = "elite_reject_context_high_caution_over",
    recommended_action: str = "",
    market_type: str = "player_points",
    selection: str = "over",
    line: float = 10.5,
    edge: float = 4.0,
    confidence: float = 0.72,
    quality_score: float = 54.0,
    prediction_date: str = PREDICTION_DATE,
    extra: dict | None = None,
) -> dict:
    row = {
        "prediction_date": prediction_date,
        "player_id": player_name.lower().replace(" ", "-"),
        "player_name": player_name,
        "team": "BOS",
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "odds": odds,
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
        "final_elite_rejection_reason": rejection_reason,
        "recommended_action": recommended_action,
        "result_status": result_status,
        "actual_value": 0.0,
        "hit": False,
        "miss": False,
        "fragility_bucket": "low",
        "survivability_bucket": "high",
    }
    if extra:
        row.update(extra)
    return row


def _by_bucket(payload: dict, breakdown: str, bucket: str) -> dict:
    rows = payload["breakdowns"][breakdown]
    return next(row for row in rows if row["bucket"] == bucket)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_american_odds_breakeven_math() -> None:
    assert american_odds_breakeven_probability(-150) == 0.6
    assert american_odds_breakeven_probability(150) == 0.4
    assert american_odds_breakeven_probability(100) == 0.5


def test_unit_profit_math_for_positive_and_negative_odds() -> None:
    assert unit_profit_for_result("hit", 150) == 1.5
    assert unit_profit_for_result("hit", -200) == 0.5
    assert unit_profit_for_result("miss", 150) == -1.0
    assert unit_profit_for_result("push", -110) == 0.0


def test_roi_excludes_pending_and_void_rows(tmp_path: Path) -> None:
    history = pd.DataFrame(
        [
            _history_row(player_name="Hit", result_status="hit", odds=100),
            _history_row(player_name="Miss", result_status="miss", odds=100),
            _history_row(player_name="Push", result_status="push", odds=100),
            _history_row(player_name="Pending", result_status="pending", odds=100),
            _history_row(player_name="Void", result_status="void", odds=100),
        ]
    )

    payload = build_threshold_pressure_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        shadow_history_df=history,
    )

    bucket = _by_bucket(payload, "by_rejection_reason", "elite_reject_context_high_caution_over")
    assert bucket["graded_rows"] == 2
    assert bucket["hit_count"] == 1
    assert bucket["miss_count"] == 1
    assert bucket["push_count"] == 1
    assert bucket["pending_count"] == 1
    assert bucket["void_count"] == 1
    assert bucket["unit_profit"] == 0.0
    assert bucket["roi_percentage"] == 0.0


def test_group_pressure_status_logic(tmp_path: Path) -> None:
    rows = []
    rows.extend(
        _history_row(
            player_name=f"Relax {index}",
            result_status="hit",
            odds=100,
            rejection_reason="profitable_reject_gate",
        )
        for index in range(31)
    )
    rows.extend(
        _history_row(
            player_name=f"Keep {index}",
            result_status="miss",
            odds=-110,
            rejection_reason="losing_reject_gate",
        )
        for index in range(31)
    )
    rows.extend(
        _history_row(
            player_name=f"Early {index}",
            result_status="hit",
            odds=100,
            rejection_reason="early_reject_gate",
        )
        for index in range(5)
    )

    payload = build_threshold_pressure_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        shadow_history_df=pd.DataFrame(rows),
    )

    assert _by_bucket(payload, "by_rejection_reason", "profitable_reject_gate")[
        "pressure_status"
    ] == "review_for_possible_relaxation"
    assert _by_bucket(payload, "by_rejection_reason", "losing_reject_gate")[
        "pressure_status"
    ] == "keep_gate"
    assert _by_bucket(payload, "by_rejection_reason", "early_reject_gate")[
        "pressure_status"
    ] == "too_early"


def test_combo_under_possible_false_reject_group_can_be_detected(tmp_path: Path) -> None:
    rows = []
    for index in range(20):
        rows.append(
            _history_row(
                player_name=f"Combo Hit {index}",
                result_status="hit",
                odds=150,
                rejection_reason="",
                market_type="player_points_rebounds",
                selection="under",
            )
        )
    for index in range(10):
        rows.append(
            _history_row(
                player_name=f"Combo Miss {index}",
                result_status="miss",
                odds=150,
                rejection_reason="",
                market_type="player_points_rebounds",
                selection="under",
            )
        )

    payload = build_threshold_pressure_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        shadow_history_df=pd.DataFrame(rows),
    )

    combo = payload["gate_pressure_summary"]["combo_under_watchlist_candidates"]
    assert combo["graded_rows"] == 30
    assert combo["pressure_status"] == "review_for_possible_relaxation"
    top_groups = payload["top_possible_false_reject_groups"]
    assert any(
        group["breakdown"] == "by_rejection_reason" and group["bucket"] == "combo_under_watchlist"
        for group in top_groups
    )


def test_post_game_actual_columns_do_not_change_pressure_inputs(tmp_path: Path) -> None:
    base = pd.DataFrame(
        [
            _history_row(
                player_name="Leak Check",
                result_status="miss",
                odds=100,
                extra={"actual_value": 999.0, "hit": True, "miss": False},
            )
        ]
    )
    changed_actuals = base.copy()
    changed_actuals["actual_value"] = -999.0
    changed_actuals["hit"] = False
    changed_actuals["miss"] = True

    first = build_threshold_pressure_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        shadow_history_df=base,
    )
    second = build_threshold_pressure_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        shadow_history_df=changed_actuals,
    )

    first_reason = _by_bucket(first, "by_rejection_reason", "elite_reject_context_high_caution_over")
    second_reason = _by_bucket(second, "by_rejection_reason", "elite_reject_context_high_caution_over")
    assert first_reason == second_reason


def test_missing_optional_artifacts_do_not_crash_and_outputs_are_written(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    rows = [
        _history_row(player_name="Output Hit", result_status="hit"),
        _history_row(player_name="Output Miss", result_status="miss"),
    ]
    _write_csv(history_root / "market_shadow_history.csv", rows)

    txt_path, json_path, csv_path, payload = write_threshold_pressure_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert txt_path.exists()
    assert json_path.exists()
    assert csv_path.exists()
    text = txt_path.read_text(encoding="utf-8")
    assert DISCLAIMER in text
    assert DISCLAIMER in render_threshold_pressure_audit(payload)
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["disclaimer"] == DISCLAIMER
    csv_df = pd.read_csv(csv_path, keep_default_na=False)
    assert not csv_df.empty
    assert "by_rejection_reason" in set(csv_df["breakdown"])


def test_write_threshold_pressure_audit_does_not_mutate_board_or_history(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    history_path = history_root / "market_shadow_history.csv"
    board_path = operator / f"full_market_board_{PREDICTION_DATE}.csv"
    rows = [_history_row(player_name="Immutable", result_status="hit")]
    _write_csv(history_path, rows)
    _write_csv(board_path, rows)
    history_before = history_path.read_text(encoding="utf-8")
    board_before = board_path.read_text(encoding="utf-8")

    write_threshold_pressure_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert history_path.read_text(encoding="utf-8") == history_before
    assert board_path.read_text(encoding="utf-8") == board_before
