from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.rejection_outcome_audit import (
    DISCLAIMER,
    build_rejection_outcome_audit,
    render_rejection_outcome_audit,
    write_rejection_outcome_audit,
)


PREDICTION_DATE = "2026-05-24"


def _history_row(
    *,
    player_name: str,
    result_status: str,
    rejection_reason: str = "elite_reject_context_high_caution_over",
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
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
        "final_elite_rejection_reason": rejection_reason,
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


def test_grouped_hit_rate_calculations(tmp_path: Path) -> None:
    history = pd.DataFrame(
        [
            _history_row(player_name="A Hit", result_status="hit", market_type="player_points"),
            _history_row(player_name="A Miss", result_status="miss", market_type="player_points"),
            _history_row(player_name="B Hit", result_status="hit", market_type="player_assists"),
        ]
    )

    payload = build_rejection_outcome_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        shadow_history_df=history,
    )

    points = _by_bucket(payload, "by_market_type", "player_points")
    assists = _by_bucket(payload, "by_market_type", "player_assists")
    assert points["graded_rows"] == 2
    assert points["hit_count"] == 1
    assert points["miss_count"] == 1
    assert points["hit_rate"] == 0.5
    assert assists["graded_rows"] == 1
    assert assists["hit_rate"] == 1.0


def test_pending_push_void_rows_are_excluded_from_hit_rate_denominator(tmp_path: Path) -> None:
    history = pd.DataFrame(
        [
            _history_row(player_name="Hit", result_status="hit"),
            _history_row(player_name="Miss", result_status="miss"),
            _history_row(player_name="Pending", result_status="pending"),
            _history_row(player_name="Push", result_status="push"),
            _history_row(player_name="Void", result_status="void"),
        ]
    )

    payload = build_rejection_outcome_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        shadow_history_df=history,
    )

    high_caution = _by_bucket(
        payload,
        "by_rejection_reason",
        "elite_reject_context_high_caution_over",
    )
    assert high_caution["graded_rows"] == 2
    assert high_caution["hit_count"] == 1
    assert high_caution["miss_count"] == 1
    assert high_caution["push_count"] == 1
    assert high_caution["void_count"] == 1
    assert high_caution["pending_count"] == 1
    assert high_caution["hit_rate"] == 0.5


def test_post_game_actuals_are_not_used_as_prediction_inputs(tmp_path: Path) -> None:
    base = pd.DataFrame(
        [
            _history_row(
                player_name="Leak Check",
                result_status="miss",
                extra={"actual_value": 999.0, "hit": True, "miss": False},
            )
        ]
    )
    changed_actuals = base.copy()
    changed_actuals["actual_value"] = -999.0
    changed_actuals["hit"] = False
    changed_actuals["miss"] = True

    first = build_rejection_outcome_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        shadow_history_df=base,
    )
    second = build_rejection_outcome_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        shadow_history_df=changed_actuals,
    )

    first_reason = _by_bucket(first, "by_rejection_reason", "elite_reject_context_high_caution_over")
    second_reason = _by_bucket(second, "by_rejection_reason", "elite_reject_context_high_caution_over")
    assert first_reason["hit_count"] == 0
    assert first_reason["miss_count"] == 1
    assert first_reason["false_reject_candidate_count"] == 0
    assert second_reason == first_reason


def test_optional_artifacts_can_enrich_meta_and_role_breakdowns(tmp_path: Path) -> None:
    history = pd.DataFrame(
        [_history_row(player_name="Meta Strong", result_status="hit", line=12.5)]
    )
    meta = pd.DataFrame(
        [
            {
                "prediction_date": PREDICTION_DATE,
                "player_id": "meta-strong",
                "player_name": "Meta Strong",
                "market_type": "player_points",
                "selection": "over",
                "line": 12.5,
                "meta_label_bucket": "shadow_strong_review_candidate",
            }
        ]
    )
    role_payload = {
        "rows": [
            {
                "prediction_date": PREDICTION_DATE,
                "player_id": "meta-strong",
                "player_name": "Meta Strong",
                "market_type": "player_points",
                "selection": "over",
                "line": 12.5,
                "role_stability_bucket": "stable",
            }
        ]
    }

    payload = build_rejection_outcome_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=tmp_path / "runtime",
        history_root=tmp_path / "history",
        shadow_history_df=history,
        meta_label_df=meta,
        role_stability_payload=role_payload,
    )

    assert _by_bucket(payload, "by_meta_label_bucket", "shadow_strong_review_candidate")["hit_count"] == 1
    assert _by_bucket(payload, "by_role_stability_bucket", "stable")["hit_count"] == 1


def test_missing_optional_artifacts_do_not_crash_and_outputs_are_written(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    rows = [
        _history_row(player_name="Output Hit", result_status="hit"),
        _history_row(player_name="Output Miss", result_status="miss"),
    ]
    _write_csv(history_root / "market_shadow_history.csv", rows)

    txt_path, json_path, csv_path, payload = write_rejection_outcome_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert txt_path.exists()
    assert json_path.exists()
    assert csv_path.exists()
    text = txt_path.read_text(encoding="utf-8")
    assert DISCLAIMER in text
    assert DISCLAIMER in render_rejection_outcome_audit(payload)
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["disclaimer"] == DISCLAIMER
    csv_df = pd.read_csv(csv_path, keep_default_na=False)
    assert not csv_df.empty
    assert "by_rejection_reason" in set(csv_df["breakdown"])


def test_write_rejection_outcome_audit_does_not_mutate_board_or_history(tmp_path: Path) -> None:
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

    write_rejection_outcome_audit(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert history_path.read_text(encoding="utf-8") == history_before
    assert board_path.read_text(encoding="utf-8") == board_before
