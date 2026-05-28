from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.incubator_performance import (
    INCUBATOR_HISTORY_COLUMNS,
    persist_daily_incubator_board,
    grade_incubator_picks,
    write_incubator_performance_report,
)
from courtvision.reporting.artifact_manifest import (
    SEVERITY_SHADOW_ONLY,
    build_artifact_manifest,
)
from scripts.write_operator_card import build_operator_card


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _incubator_row(
    player: str,
    *,
    prediction_date: str = "2026-05-28",
    market_type: str = "player_points",
    selection: str = "over",
    line: float = 13.5,
    odds: int = -110,
    edge: float = 3.5,
    confidence: float = 0.76,
    quality_score: float = 65.0,
    context_caution_level: str = "high",
    source_rejection_reason: str = "elite_reject_context_high_caution_over",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player": player,
        "player_id": player.lower().replace(" ", "-"),
        "team": "OKC",
        "opponent": "DAL",
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "odds": odds,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "context_caution_level": context_caution_level,
        "source_rejection_reason": source_rejection_reason,
    }


def _quality_payload(prediction_date: str, *, full_market_count: int, incubator_count: int) -> dict:
    return {
        "run_identity": {"prediction_date": prediction_date},
        "run_health_status": "NO_BET",
        "run_health_reason": "No stakeable picks are available.",
        "slate_provider_counts": {
            "games_count": 1,
            "raw_odds_rows_count": full_market_count,
            "normalized_odds_rows_count": full_market_count,
            "live_odds_count": full_market_count,
            "synthetic_or_fallback_odds_count": 0,
            "provider_breakdown": {"line_source": {"fixture_live_market": full_market_count}},
        },
        "candidate_funnel": {
            "raw_candidates_count": full_market_count,
            "rejected_candidates_count": full_market_count,
            "full_market_board_count": full_market_count,
            "elite_board_count": 0,
            "sgp_board_count": 0,
            "kelly_rows_count": 0,
            "incubator_board_count": incubator_count,
        },
        "incubator_board": {
            "path": f"outputs/runtime/operator/incubator_board_{prediction_date}.csv",
            "row_count": incubator_count,
            "shadow_only": True,
            "paper_only": True,
            "real_money_eligible": False,
            "note": "Incubator rows are paper-only candidates for model learning and are not staking inputs.",
            "source": f"outputs/runtime/operator/full_market_board_{prediction_date}.csv",
        },
        "kelly_safety_summary": {
            "total_rows": 0,
            "kelly_eligible_count": 0,
            "manual_review_required_count": 0,
            "review_before_bet_count": 0,
            "review_policy_hold_count": 0,
        },
        "manual_review_required_count": 0,
        "same_opponent_under_warning_count": 0,
        "high_caution_over_watchlist": {"row_count": 0},
        "date_isolation_check": {"status": "ok"},
    }


def test_rule1_upsert_separate_history(tmp_path: Path) -> None:
    # 1. Incubator rows upsert into separate incubator_history.csv.
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    
    clean_row = _incubator_row("Jalen Williams")
    _write_csv(
        runtime_root / "operator" / f"incubator_board_{prediction_date}.csv",
        [clean_row],
    )
    
    res = persist_daily_incubator_board(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    assert res["appended_rows"] == 1
    assert res["total_rows"] == 1
    
    history_file = history_root / "incubator_history.csv"
    assert history_file.exists()
    
    history_df = pd.read_csv(history_file, keep_default_na=False)
    assert len(history_df) == 1
    assert history_df.iloc[0]["player"] == "Jalen Williams"
    assert history_df.iloc[0]["player_id"] == "jalen-williams"
    assert history_df.iloc[0]["result_status"] == "pending"


def test_rule2_incubator_rows_do_not_contaminate_pick_history(tmp_path: Path) -> None:
    # 2. Incubator rows do not enter pick_history.csv.
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    
    clean_row = _incubator_row("Jalen Williams")
    _write_csv(
        runtime_root / "operator" / f"incubator_board_{prediction_date}.csv",
        [clean_row],
    )
    
    persist_daily_incubator_board(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    
    pick_history_file = history_root / "pick_history.csv"
    assert not pick_history_file.exists() or pd.read_csv(pick_history_file).empty


def test_rule3_no_duplicates_in_history(tmp_path: Path) -> None:
    # 3. Duplicate incubator rows do not duplicate history (upsert behavior).
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    
    clean_row = _incubator_row("Jalen Williams")
    board_path = runtime_root / "operator" / f"incubator_board_{prediction_date}.csv"
    _write_csv(board_path, [clean_row])
    
    # Run once
    persist_daily_incubator_board(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    # Run twice
    res = persist_daily_incubator_board(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    assert res["total_rows"] == 1
    
    history_df = pd.read_csv(history_root / "incubator_history.csv", keep_default_na=False)
    assert len(history_df) == 1


def test_rule4_grading_completed_picks(tmp_path: Path, monkeypatch) -> None:
    # 4. Completed incubator rows can be graded hit/miss/push.
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    
    # 3 mock incubator rows: one will hit, one will miss, one will push
    rows = [
        _incubator_row("Hit Player", line=10.5, selection="over"),
        _incubator_row("Miss Player", line=15.5, selection="over"),
        _incubator_row("Push Player", line=12.0, selection="over"),
    ]
    _write_csv(
        runtime_root / "operator" / f"incubator_board_{prediction_date}.csv",
        rows,
    )
    persist_daily_incubator_board(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    
    # Mock player stats fallback
    stats = pd.DataFrame([
        {"player_name": "Hit Player", "team_abbr": "OKC", "game_date": prediction_date, "pts": 12.0},
        {"player_name": "Miss Player", "team_abbr": "OKC", "game_date": prediction_date, "pts": 14.0},
        {"player_name": "Push Player", "team_abbr": "OKC", "game_date": prediction_date, "pts": 12.0},
    ])
    
    import scripts.history_tracking as history_tracking
    
    monkeypatch.setattr(history_tracking, "_load_actual_results_for_date", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(history_tracking, "_load_player_stats_for_date", lambda *_args, **_kwargs: stats.copy())
    monkeypatch.setattr(history_tracking, "_load_games_for_date", lambda *_args, **_kwargs: pd.DataFrame())
    
    res = grade_incubator_picks(
        history_root=history_root,
        runtime_root=runtime_root,
        prediction_date=prediction_date,
    )
    assert res["updated_rows"] == 3
    
    history_df = pd.read_csv(history_root / "incubator_history.csv", keep_default_na=False)
    hit_row = history_df[history_df["player"] == "Hit Player"].iloc[0]
    miss_row = history_df[history_df["player"] == "Miss Player"].iloc[0]
    push_row = history_df[history_df["player"] == "Push Player"].iloc[0]
    
    assert hit_row["result_status"] == "hit"
    assert miss_row["result_status"] == "miss"
    assert push_row["result_status"] == "push"


def test_rule5_open_games_remain_pending(tmp_path: Path, monkeypatch) -> None:
    # 5. Open games remain pending/open_game_pending.
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    
    row = _incubator_row("Pending Player")
    _write_csv(
        runtime_root / "operator" / f"incubator_board_{prediction_date}.csv",
        [row],
    )
    persist_daily_incubator_board(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    
    # Mock games showing NOT final
    games = pd.DataFrame([
        {"id": "game-1", "home_team_abbr": "OKC", "visitor_team_abbr": "DAL", "status": "Scheduled"}
    ])
    
    import scripts.history_tracking as history_tracking
    
    monkeypatch.setattr(history_tracking, "_load_actual_results_for_date", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(history_tracking, "_load_player_stats_for_date", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(history_tracking, "_load_games_for_date", lambda *_args, **_kwargs: games.copy())
    
    res = grade_incubator_picks(
        history_root=history_root,
        runtime_root=runtime_root,
        prediction_date=prediction_date,
    )
    assert res["updated_rows"] == 0
    
    history_df = pd.read_csv(history_root / "incubator_history.csv", keep_default_na=False)
    assert history_df.iloc[0]["result_status"] in {"pending", "open_game_pending"}


def test_rule6_no_effect_on_final_decision(tmp_path: Path, monkeypatch) -> None:
    # 6. Incubator performance does not affect final_decision.
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    
    candidate = _incubator_row("Incubator Row")
    
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [candidate])
    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=list(candidate.keys()))
    _write_csv(operator / f"near_elite_review_{prediction_date}.csv", [], columns=list(candidate.keys()))
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_csv(operator / f"kelly_stakes_{prediction_date}.csv", [], columns=["kelly_eligible", "stake_amount"])
    
    _write_csv(operator / f"incubator_board_{prediction_date}.csv", [candidate])
    
    # Mock incubator performance JSON
    _write_json(diagnostics / f"incubator_performance_report_{prediction_date}.json", {
        "overall": {
            "total_picks": 1,
            "graded_count": 1,
            "pending_count": 0,
            "win_rate": 1.0,
        }
    })
    
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(prediction_date, full_market_count=1, incubator_count=1),
    )
    _write_json(diagnostics / f"board_diagnostics_{prediction_date}.json", {"board_counts": {"qualified_pool": 1}})
    _write_json(diagnostics / f"market_shadow_grading_{prediction_date}.json", {})
    _write_json(diagnostics / f"injury_context_diagnostics_{prediction_date}.json", {})
    _write_json(diagnostics / f"game_context_{prediction_date}.json", {})
    
    # Mock other report JSONs so build_operator_card doesn't report unavailable status warnings
    for report in ("clv_market_movement", "calibration_bucket_report", "player_role_stability",
                   "meta_label_promotion_shadow", "meta_label_rules_performance", "feature_completeness_tracker"):
        _write_json(diagnostics / f"{report}_{prediction_date}.json", {"summary": {}})
    
    text, payload = build_operator_card(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    
    # final_decision remains NO BET since elite picks are empty
    assert payload["final_decision"] == "NO BET"
    assert payload["elite_count"] == 0
    assert payload["incubator_board_count"] == 1


def test_rule7_no_effect_on_kelly(tmp_path: Path) -> None:
    # 7. Incubator performance does not affect Kelly.
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    
    # Kelly stakes should remain empty or unaffected even with active incubator rows
    clean_row = _incubator_row("Jalen Williams")
    _write_csv(
        runtime_root / "operator" / f"incubator_board_{prediction_date}.csv",
        [clean_row],
    )
    
    persist_daily_incubator_board(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    
    kelly_file = runtime_root / "operator" / f"kelly_stakes_{prediction_date}.csv"
    assert not kelly_file.exists() or pd.read_csv(kelly_file).empty


def test_rule8_report_artifacts_written(tmp_path: Path) -> None:
    # 8. Report artifacts are written (.txt, .json, .csv).
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    
    row = _incubator_row("Jalen Williams")
    _write_csv(
        runtime_root / "operator" / f"incubator_board_{prediction_date}.csv",
        [row],
    )
    
    persist_daily_incubator_board(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    
    txt_p, json_p, csv_p, report = write_incubator_performance_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    
    assert txt_p.exists()
    assert json_p.exists()
    assert csv_p.exists()
    
    assert report["overall"]["total_picks"] == 1
    assert report["overall"]["pending_count"] == 1


def test_rule9_manifest_includes_performance(tmp_path: Path) -> None:
    # 9. Artifact manifest includes incubator performance outputs.
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    
    # Write core boards to satisfy manifest
    for name in ("elite_board", "full_market_board", "sgp_board"):
        _write_csv(runtime_root / "operator" / f"{name}_{prediction_date}.csv", [], columns=["player_name"])
        
    # Write incubator board & report files
    _write_csv(runtime_root / "operator" / f"incubator_board_{prediction_date}.csv", [], columns=["player"])
    runtime_root.joinpath("operator").mkdir(parents=True, exist_ok=True)
    runtime_root.joinpath("diagnostics").mkdir(parents=True, exist_ok=True)
    runtime_root.joinpath("operator", f"incubator_performance_report_{prediction_date}.txt").write_text("txt", encoding="utf-8")
    runtime_root.joinpath("operator", f"incubator_performance_report_{prediction_date}.csv").write_text("csv", encoding="utf-8")
    runtime_root.joinpath("diagnostics", f"incubator_performance_report_{prediction_date}.json").write_text("{}", encoding="utf-8")
    
    manifest = build_artifact_manifest(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        generated_at="2026-05-28T12:00:00Z",
    )
    
    txt_art = next(item for item in manifest["artifacts"] if item["name"] == "incubator_performance_report_txt")
    json_art = next(item for item in manifest["artifacts"] if item["name"] == "incubator_performance_report_json")
    csv_art = next(item for item in manifest["artifacts"] if item["name"] == "incubator_performance_report_csv")
    
    assert txt_art["exists"] is True
    assert json_art["exists"] is True
    assert csv_art["exists"] is True
    
    assert txt_art["severity"] == SEVERITY_SHADOW_ONLY
    assert json_art["severity"] == SEVERITY_SHADOW_ONLY
    assert csv_art["severity"] == SEVERITY_SHADOW_ONLY


def test_rule10_empty_incubator_board_handling(tmp_path: Path) -> None:
    # 10. Empty incubator board is handled safely.
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    
    _write_csv(
        runtime_root / "operator" / f"incubator_board_{prediction_date}.csv",
        [],
    )
    
    res = persist_daily_incubator_board(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    assert res["appended_rows"] == 0
    
    txt_p, json_p, csv_p, report = write_incubator_performance_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    assert report["overall"]["total_picks"] == 0
