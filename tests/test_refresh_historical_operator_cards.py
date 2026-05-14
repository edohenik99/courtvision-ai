from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.refresh_historical_operator_cards import (
    FAILED,
    REFRESHED,
    SKIPPED_NOT_STALE,
    WOULD_REFRESH,
    refresh_historical_operator_cards,
)


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _candidate(prediction_date: str) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": "Fixture Player",
        "team_abbr": "BOS",
        "opponent": "NYK",
        "game_id": "game-1",
        "home_away": "home",
        "market_type": "player_points",
        "selection": "over",
        "line": 12.5,
        "sportsbook_line": 12.5,
        "odds": -110,
        "edge": 3.25,
        "confidence": 0.78,
        "quality_score": 72.0,
        "context_caution_level": "low",
    }


def _quality_payload(prediction_date: str) -> dict:
    return {
        "run_identity": {"prediction_date": prediction_date},
        "run_health_status": "HEALTHY",
        "run_health_reason": "fixture",
        "slate_provider_counts": {
            "games_count": 1,
            "raw_odds_rows_count": 1,
            "normalized_odds_rows_count": 1,
            "live_odds_count": 1,
            "synthetic_or_fallback_odds_count": 0,
            "provider_breakdown": {"line_source": {"live_market": 1}},
        },
        "candidate_funnel": {
            "full_market_board_count": 1,
            "elite_board_count": 1,
            "sgp_board_count": 0,
            "kelly_rows_count": 0,
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


def _seed_refresh_sources(
    tmp_path: Path,
    prediction_date: str,
    *,
    stale_card: bool = True,
    missing_card: bool = False,
    missing_sources: set[str] | None = None,
) -> tuple[Path, Path, dict[str, Path]]:
    missing_sources = missing_sources or set()
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    row = _candidate(prediction_date)
    paths = {
        "operator_card": operator / f"operator_card_{prediction_date}.txt",
        "elite_board": operator / f"elite_board_{prediction_date}.csv",
        "full_market_board": operator / f"full_market_board_{prediction_date}.csv",
        "sgp_board": operator / f"sgp_board_{prediction_date}.csv",
        "daily_summary": operator / f"daily_summary_{prediction_date}.txt",
        "quality_summary": operator / f"quality_summary_{prediction_date}.txt",
        "quality_summary_json": operator / f"quality_summary_{prediction_date}.json",
        "completion_audit_json": diagnostics / f"completion_state_audit_{prediction_date}.json",
        "completion_audit_text": operator / f"completion_state_audit_{prediction_date}.txt",
        "board_diagnostics": diagnostics / f"board_diagnostics_{prediction_date}.json",
        "market_shadow_grading": diagnostics / f"market_shadow_grading_{prediction_date}.json",
        "pick_history": history_root / "pick_history.csv",
    }

    if "elite_board" not in missing_sources:
        _write_csv(paths["elite_board"], [row])
    if "full_market_board" not in missing_sources:
        _write_csv(paths["full_market_board"], [row])
    _write_csv(paths["sgp_board"], [], columns=["prediction_date"])
    _write_text(paths["daily_summary"], f"Daily Summary - {prediction_date}\n")
    _write_text(paths["quality_summary"], f"Quality Summary - {prediction_date}\n")
    if "quality_summary_json" not in missing_sources:
        _write_json(paths["quality_summary_json"], _quality_payload(prediction_date))
    _write_json(
        paths["completion_audit_json"],
        {
            "report_agreement_status": "COMPLETE",
            "real_pick_pending_count": 0,
            "shadow_pending_count": 0,
            "shadow_open_game_pending_count": 0,
            "shadow_stale_pending_count": 0,
            "paper_pending_count": 0,
            "paper_open_game_pending_count": 0,
            "paper_stale_pending_count": 0,
            "agreement_issues": [],
            "warnings": [],
            "details": {
                "shadow_pending_taxonomy_source": "history",
                "paper_pending_taxonomy_source": "history",
            },
        },
    )
    _write_text(paths["completion_audit_text"], f"Completion State Audit - {prediction_date}\n")
    if "board_diagnostics" not in missing_sources:
        _write_json(paths["board_diagnostics"], {"board_counts": {"qualified_pool": 1, "rejected": 0}})
    _write_json(
        paths["market_shadow_grading"],
        {"totals": {"total_picks": 1, "graded_picks": 1, "pending_picks": 0, "hit_rate": 1.0}},
    )
    _write_csv(paths["pick_history"], [{"prediction_date": prediction_date, "result_status": "hit"}])

    if not missing_card:
        if stale_card:
            _write_text(
                paths["operator_card"],
                "\n".join(
                    [
                        f"COURTVISION DAILY CARD - {prediction_date}",
                        "final_decision: BETTABLE",
                        "Completion State Audit",
                        "- report_agreement_status: COMPLETE",
                        "",
                    ]
                ),
            )
        else:
            _write_text(
                paths["operator_card"],
                "\n".join(
                    [
                        f"COURTVISION DAILY CARD - {prediction_date}",
                        "final_decision: BETTABLE",
                        "Completion State Audit",
                        "- recommended action: slate closed / no action required",
                        "",
                    ]
                ),
            )
    return runtime_root, history_root, paths


def _snapshot(paths: list[Path]) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in paths}


def test_dry_run_does_not_modify_operator_card(tmp_path: Path) -> None:
    prediction_date = "2026-05-10"
    runtime_root, history_root, paths = _seed_refresh_sources(tmp_path, prediction_date)
    before = paths["operator_card"].read_bytes()

    payload = refresh_historical_operator_cards(
        start_date=prediction_date,
        end_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        only_stale=True,
        dry_run=True,
        today="2026-06-01",
    )

    assert payload["dates"][0]["action"] == WOULD_REFRESH
    assert paths["operator_card"].read_bytes() == before


def test_only_stale_refreshes_card_missing_recommended_action(tmp_path: Path) -> None:
    prediction_date = "2026-05-10"
    runtime_root, history_root, paths = _seed_refresh_sources(tmp_path, prediction_date, stale_card=True)

    payload = refresh_historical_operator_cards(
        start_date=prediction_date,
        end_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        only_stale=True,
        today="2026-06-01",
    )

    text = paths["operator_card"].read_text(encoding="utf-8")
    assert payload["dates"][0]["action"] == REFRESHED
    assert "- recommended action: slate closed / no action required" in text
    assert "Completion State Audit" in text


def test_only_stale_skips_fresh_card_with_recommended_action(tmp_path: Path) -> None:
    prediction_date = "2026-05-11"
    runtime_root, history_root, paths = _seed_refresh_sources(tmp_path, prediction_date, stale_card=False)
    before = paths["operator_card"].read_bytes()

    payload = refresh_historical_operator_cards(
        start_date=prediction_date,
        end_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        only_stale=True,
        today="2026-06-01",
    )

    assert payload["dates"][0]["action"] == SKIPPED_NOT_STALE
    assert paths["operator_card"].read_bytes() == before


def test_missing_operator_card_can_be_regenerated_when_sources_exist(tmp_path: Path) -> None:
    prediction_date = "2026-05-12"
    runtime_root, history_root, paths = _seed_refresh_sources(tmp_path, prediction_date, missing_card=True)

    payload = refresh_historical_operator_cards(
        start_date=prediction_date,
        end_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        only_stale=True,
        today="2026-06-01",
    )

    assert payload["dates"][0]["action"] == REFRESHED
    assert paths["operator_card"].exists()
    assert "- recommended action: slate closed / no action required" in paths["operator_card"].read_text(encoding="utf-8")


def test_refresh_does_not_modify_source_artifacts(tmp_path: Path) -> None:
    prediction_date = "2026-05-13"
    runtime_root, history_root, paths = _seed_refresh_sources(tmp_path, prediction_date, stale_card=True)
    protected = [
        paths["completion_audit_json"],
        paths["completion_audit_text"],
        paths["elite_board"],
        paths["full_market_board"],
        paths["sgp_board"],
        paths["daily_summary"],
        paths["quality_summary"],
        paths["quality_summary_json"],
        paths["pick_history"],
    ]
    before = _snapshot(protected)

    payload = refresh_historical_operator_cards(
        start_date=prediction_date,
        end_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        only_stale=True,
        today="2026-06-01",
    )

    assert payload["dates"][0]["action"] == REFRESHED
    assert _snapshot(protected) == before


def test_failure_reported_when_required_sources_are_missing(tmp_path: Path) -> None:
    prediction_date = "2026-05-14"
    runtime_root, history_root, paths = _seed_refresh_sources(
        tmp_path,
        prediction_date,
        missing_card=True,
        missing_sources={"full_market_board"},
    )

    payload = refresh_historical_operator_cards(
        start_date=prediction_date,
        end_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        only_stale=True,
        today="2026-06-01",
    )

    row = payload["dates"][0]
    assert row["action"] == FAILED
    assert row["reason"] == "missing_required_source_artifacts"
    assert row["missing_required_sources"] == ["full_market_board"]
    assert not paths["operator_card"].exists()
