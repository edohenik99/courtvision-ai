from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.write_operator_card import write_operator_card_outputs


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
    line: float = 12.5,
    edge: float = 3.25,
    quality_score: float = 72.0,
    confidence: float = 0.78,
    manual_review_required: bool = False,
    same_opponent_under_warning: bool = False,
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "team_abbr": "BOS",
        "opponent": "NYK",
        "game_id": "game-1",
        "home_away": "home",
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "sportsbook_line": line,
        "odds": -110,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "context_caution_level": "low",
        "manual_review_required": manual_review_required,
        "manual_review_reason": "same_opponent_under_warning" if manual_review_required else "",
        "same_opponent_under_warning": same_opponent_under_warning,
        "same_opponent_warning_reason": "same_opponent_under_warning" if same_opponent_under_warning else "",
    }


def _quality_payload(
    prediction_date: str,
    *,
    elite_count: int,
    full_market_count: int,
    sgp_count: int = 0,
    kelly_rows: int = 0,
    kelly_eligible: int = 0,
    manual_review_count: int = 0,
    review_before_bet_count: int = 0,
    hold_count: int = 0,
    same_opponent_count: int = 0,
    high_caution_count: int = 0,
    run_health_status: str = "HEALTHY",
    games_count: int = 1,
) -> dict:
    run_health_reason = "fixture"
    if run_health_status == "NO_BET":
        run_health_reason = "No stakeable picks are available."
    return {
        "run_identity": {"prediction_date": prediction_date},
        "run_health_status": run_health_status,
        "run_health_reason": run_health_reason,
        "slate_provider_counts": {
            "games_count": games_count,
            "raw_odds_rows_count": full_market_count,
            "normalized_odds_rows_count": full_market_count,
            "live_odds_count": full_market_count,
            "synthetic_or_fallback_odds_count": 0,
            "provider_breakdown": {"line_source": {"live_market": full_market_count}},
        },
        "candidate_funnel": {
            "full_market_board_count": full_market_count,
            "elite_board_count": elite_count,
            "sgp_board_count": sgp_count,
            "kelly_rows_count": kelly_rows,
        },
        "kelly_safety_summary": {
            "total_rows": kelly_rows,
            "kelly_eligible_count": kelly_eligible,
            "manual_review_required_count": 0,
            "review_before_bet_count": review_before_bet_count,
            "review_policy_hold_count": hold_count,
        },
        "manual_review_required_count": manual_review_count,
        "same_opponent_under_warning_count": same_opponent_count,
        "high_caution_over_watchlist": {"row_count": high_caution_count},
        "date_isolation_check": {"status": "ok"},
    }


def _seed_required_json(runtime_root: Path, prediction_date: str) -> None:
    diagnostics = runtime_root / "diagnostics"
    _write_json(
        diagnostics / f"board_diagnostics_{prediction_date}.json",
        {"board_counts": {"qualified_pool": 2, "rejected": 0}},
    )
    _write_json(
        diagnostics / f"market_shadow_grading_{prediction_date}.json",
        {
            "totals": {"total_picks": 2, "graded_picks": 1, "pending_picks": 1, "hit_rate": 0.5},
            "kelly_decision_performance": {"status": "insufficient_sample"},
        },
    )
    _write_json(
        diagnostics / f"injury_context_diagnostics_{prediction_date}.json",
        {"normalized_rows": 4, "candidate_player_matches": 1},
    )
    _write_json(
        diagnostics / f"game_context_{prediction_date}.json",
        {"rows": 2, "game_context_suppressed_count": 0, "stale_team_not_in_game_count": 0},
    )


def _seed_history(history_root: Path) -> None:
    _write_csv(
        history_root / "pick_history.csv",
        [
            {"prediction_date": "2026-05-01", "result_status": "hit"},
            {"prediction_date": "2026-05-02", "result_status": "miss"},
            {"prediction_date": "2026-05-03", "result_status": "hit"},
        ],
    )


def _write_completion_audit_json(
    runtime_root: Path,
    prediction_date: str,
    *,
    status: str = "COMPLETE_WITH_SHADOW_OPEN_NOISE",
    real_pending: int = 0,
    shadow_pending: int = 57,
    shadow_open: int = 57,
    shadow_stale: int = 0,
    paper_pending: int = 25,
    paper_open: int = 25,
    paper_stale: int = 0,
    agreement_issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> None:
    _write_json(
        runtime_root / "diagnostics" / f"completion_state_audit_{prediction_date}.json",
        {
            "report_agreement_status": status,
            "real_pick_pending_count": real_pending,
            "shadow_pending_count": shadow_pending,
            "shadow_open_game_pending_count": shadow_open,
            "shadow_stale_pending_count": shadow_stale,
            "paper_pending_count": paper_pending,
            "paper_open_game_pending_count": paper_open,
            "paper_stale_pending_count": paper_stale,
            "agreement_issues": agreement_issues or [],
            "warnings": warnings or [],
            "details": {
                "shadow_pending_taxonomy_source": "pending_repair_audit",
                "paper_pending_taxonomy_source": "pending_repair_audit",
            },
        },
    )


def _seed_basic_operator_card_artifacts(
    runtime_root: Path,
    history_root: Path,
    prediction_date: str,
) -> None:
    operator = runtime_root / "operator"
    row = _candidate(prediction_date)

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [row])
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [row])
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(prediction_date, elite_count=1, full_market_count=1),
    )
    _seed_required_json(runtime_root, prediction_date)
    _seed_history(history_root)


def test_operator_card_completion_recommendation_complete_clean(tmp_path: Path) -> None:
    prediction_date = "2026-05-10"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_basic_operator_card_artifacts(runtime_root, history_root, prediction_date)
    _write_completion_audit_json(
        runtime_root,
        prediction_date,
        status="COMPLETE",
        shadow_pending=0,
        shadow_open=0,
        paper_pending=0,
        paper_open=0,
    )

    output_path, _payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert "- report_agreement_status: COMPLETE" in text
    assert "- recommended action: slate closed / no action required" in text


def test_operator_card_renders_unsupported_active_market_drops(tmp_path: Path) -> None:
    prediction_date = "2026-05-10"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_basic_operator_card_artifacts(runtime_root, history_root, prediction_date)
    _write_json(
        runtime_root / "operator" / f"quality_summary_{prediction_date}.json",
        {
            **_quality_payload(prediction_date, elite_count=1, full_market_count=1),
            "unsupported_active_operator_markets": {
                "rejection_reason": "unsupported_active_operator_market",
                "total_rows_dropped": 6,
                "counts_by_market_type": {
                    "player_blocks": 3,
                    "player_steals": 3,
                },
            },
        },
    )

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert "- unsupported active markets dropped: 6 (player_blocks=3, player_steals=3)" in text
    assert payload["unsupported_active_operator_markets"]["total_rows_dropped"] == 6


def test_operator_card_renders_identity_quarantine_summary_with_elite_picks(tmp_path: Path) -> None:
    prediction_date = "2026-05-10"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_basic_operator_card_artifacts(runtime_root, history_root, prediction_date)
    _write_json(
        runtime_root / "operator" / f"quality_summary_{prediction_date}.json",
        {
            **_quality_payload(
                prediction_date,
                elite_count=1,
                full_market_count=1,
                run_health_status="HEALTHY",
            ),
            "identity_quarantine": {
                "rejection_reason": "identity_quarantine",
                "total_rows_dropped": 1,
                "counts_by_reason": {"outside_team_identity": 1},
            },
        },
    )

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert "Final Decision: NO BET" not in text
    assert text.count("- identity quarantined: 1 (outside_team_identity=1)") == 1
    assert payload["identity_quarantine"]["total_rows_dropped"] == 1


def test_operator_card_zero_unsupported_active_market_drops_stays_quiet(tmp_path: Path) -> None:
    prediction_date = "2026-05-10"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_basic_operator_card_artifacts(runtime_root, history_root, prediction_date)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert "unsupported active markets dropped" not in text
    assert payload["unsupported_active_operator_markets"]["total_rows_dropped"] == 0


def test_operator_card_renders_completion_audit_when_json_exists(tmp_path: Path) -> None:
    prediction_date = "2026-05-13"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    elite_row = _candidate(prediction_date, player_name="Completed Elite")

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [elite_row])
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [elite_row])
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_csv(operator / f"kelly_stakes_{prediction_date}.csv", [elite_row | {"kelly_eligible": True}])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(
            prediction_date,
            elite_count=1,
            full_market_count=1,
            kelly_rows=1,
            kelly_eligible=1,
        ),
    )
    _seed_required_json(runtime_root, prediction_date)
    _write_completion_audit_json(runtime_root, prediction_date)
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert payload["completion_state_audit_status"] == "COMPLETE_WITH_SHADOW_OPEN_NOISE"
    assert "Completion State Audit" in text
    assert "- report_agreement_status: COMPLETE_WITH_SHADOW_OPEN_NOISE" in text
    assert "- real_pick_pending_count: 0" in text
    assert (
        "- market_shadow_history: pending=57, open_game_pending=57, "
        "stale_pending=0, taxonomy_source=pending_repair_audit"
    ) in text
    assert (
        "- paper_kelly_history: pending=25, open_game_pending=25, "
        "stale_pending=0, taxonomy_source=pending_repair_audit"
    ) in text
    assert "- agreement issue count: none" in text
    assert "- warning count: none" in text
    assert "- recommended action: real picks closed / ignore shadow-paper open-game noise" in text


def test_operator_card_completion_recommendation_real_pending(tmp_path: Path) -> None:
    prediction_date = "2026-05-16"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_basic_operator_card_artifacts(runtime_root, history_root, prediction_date)
    _write_completion_audit_json(
        runtime_root,
        prediction_date,
        status="PARTIAL",
        real_pending=1,
        shadow_pending=0,
        shadow_open=0,
        paper_pending=0,
        paper_open=0,
    )

    output_path, _payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert "- real_pick_pending_count: 1" in text
    assert "- recommended action: inspect grading before trusting results" in text


def test_operator_card_completion_recommendation_warnings_or_issues(tmp_path: Path) -> None:
    prediction_date = "2026-05-17"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _seed_basic_operator_card_artifacts(runtime_root, history_root, prediction_date)
    _write_completion_audit_json(
        runtime_root,
        prediction_date,
        status="COMPLETE",
        shadow_pending=0,
        shadow_open=0,
        paper_pending=0,
        paper_open=0,
        agreement_issues=["daily_summary_pending_grading_mismatch"],
        warnings=["source quality warning"],
    )

    output_path, _payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert "- agreement issue count: 1" in text
    assert "- warning count: 1" in text
    assert "- recommended action: inspect completion audit before trusting results" in text


def test_operator_card_handles_missing_completion_audit_json_gracefully(tmp_path: Path) -> None:
    prediction_date = "2026-05-15"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    elite_row = _candidate(prediction_date, player_name="Missing Audit Elite")

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [elite_row])
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [elite_row])
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(prediction_date, elite_count=1, full_market_count=1),
    )
    _seed_required_json(runtime_root, prediction_date)
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert payload["completion_state_audit_status"] == "missing"
    assert "Completion State Audit" in text
    assert "- Completion audit: missing" in text
    assert (
        "- recommended action: run scripts/write_completion_state_audit.py "
        "--prediction-date 2026-05-15"
    ) in text


def test_operator_card_no_slate_still_renders_cleanly(tmp_path: Path) -> None:
    prediction_date = "2026-05-14"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    columns = list(_candidate(prediction_date).keys())

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(
            prediction_date,
            elite_count=0,
            full_market_count=0,
            run_health_status="NO_BET",
            games_count=0,
        ),
    )
    _seed_required_json(runtime_root, prediction_date)
    _write_completion_audit_json(
        runtime_root,
        prediction_date,
        status="COMPLETE",
        shadow_pending=0,
        shadow_open=0,
        paper_pending=0,
        paper_open=0,
    )
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert payload["final_decision"] == "NO BET"
    assert "COURTVISION DAILY CARD - 2026-05-14" in text
    assert "final_decision: NO BET" in text
    assert "- games count: 0" in text
    assert "Top Candidate Preview\n----------------------------------------\nn/a" in text
    assert "- report_agreement_status: COMPLETE" in text
    assert "- recommended action: slate closed / no action required" in text


def test_operator_card_empty_elite_shows_no_bet_and_candidate_preview(tmp_path: Path) -> None:
    prediction_date = "2026-05-12"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    columns = list(_candidate(prediction_date).keys())

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(
        operator / f"full_market_board_{prediction_date}.csv",
        [
            _candidate(
                prediction_date,
                player_name="Review Under",
                selection="under",
                edge=-2.5,
                manual_review_required=True,
                same_opponent_under_warning=True,
            ),
            _candidate(
                prediction_date,
                player_name="Rebound Candidate",
                market_type="player_rebounds",
                quality_score=64.0,
            ),
        ],
    )
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_csv(operator / f"high_caution_over_watchlist_{prediction_date}.csv", [_candidate(prediction_date)])
    _write_csv(
        operator / f"combo_under_watchlist_{prediction_date}.csv",
        [_candidate(prediction_date, market_type="player_points_rebounds", selection="under", edge=-4.1)],
    )
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(
            prediction_date,
            elite_count=0,
            full_market_count=2,
            manual_review_count=1,
            same_opponent_count=1,
            high_caution_count=1,
            run_health_status="NO_BET",
        ),
    )
    _seed_required_json(runtime_root, prediction_date)
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert payload["final_decision"] == "NO BET"
    assert "COURTVISION DAILY CARD - 2026-05-12" in text
    assert "NO ELITE PICKS - all candidates were filtered" in text
    assert "Top Candidate Preview" in text
    assert "player_points: 1" in text
    assert "player_rebounds: 1" in text
    assert "MANUAL_REVIEW_REQUIRED" in text
    assert "same-opponent warning count: 1" in text
    assert "- operator_card:" in text and "[ok]" in text


def test_operator_card_marks_review_required_for_kelly_hold(tmp_path: Path) -> None:
    prediction_date = "2026-05-13"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    elite_row = _candidate(
        prediction_date,
        player_name="Held Elite",
        manual_review_required=True,
        same_opponent_under_warning=True,
    )

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [elite_row])
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [elite_row])
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_csv(
        operator / f"kelly_stakes_{prediction_date}.csv",
        [
            {
                "prediction_date": prediction_date,
                "player_name": "Held Elite",
                "market_type": "player_points",
                "selection": "over",
                "line": 12.5,
                "odds": -110,
                "kelly_eligible": False,
                "review_before_bet": True,
                "operator_action": "DO_NOT_BET_UNTIL_REVIEWED",
                "operator_note": "manual_review_required",
            }
        ],
    )
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(
            prediction_date,
            elite_count=1,
            full_market_count=1,
            kelly_rows=1,
            review_before_bet_count=1,
            hold_count=1,
            manual_review_count=1,
            same_opponent_count=1,
        ),
    )
    _seed_required_json(runtime_root, prediction_date)
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert payload["final_decision"] == "REVIEW REQUIRED"
    assert "review_before_bet count: 1" in text
    assert "Held Elite" in text
    assert "REVIEW_BEFORE_BET" in text
    assert "Why Review Required?" in text
    assert "- 1 elite candidate requires manual review." in text
    assert "- 1 candidate is marked review_before_bet." in text
    assert "- 1 same-opponent UNDER warning is present." in text
    assert "- Kelly exists, but stake should not be treated as clean until review is complete." in text
    assert (
        "Final Decision\n----------------------------------------\n"
        "REVIEW REQUIRED — elite candidates exist, but review flags are present."
    ) in text


def test_operator_card_degraded_when_required_artifact_is_missing(tmp_path: Path) -> None:
    prediction_date = "2026-05-14"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    elite_row = _candidate(prediction_date)

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [elite_row])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(prediction_date, elite_count=1, full_market_count=1, kelly_rows=1, kelly_eligible=1),
    )
    _seed_required_json(runtime_root, prediction_date)
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert payload["final_decision"] == "DEGRADED"
    assert "Missing Required Artifacts" in text
    assert f"full_market_board_{prediction_date}.csv" in text

def test_operator_card_no_bet_preview_rows_are_shadow_only_not_clear(tmp_path: Path) -> None:
    prediction_date = "2026-05-15"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"

    candidate = _candidate(prediction_date)
    candidate["recommended_action"] = "CLEAR"
    candidate["qualification_reason"] = ""
    columns = list(candidate.keys())

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [candidate], columns=columns)
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(
            prediction_date,
            elite_count=0,
            full_market_count=1,
            run_health_status="NO_BET",
            games_count=1,
        ),
    )
    _seed_required_json(runtime_root, prediction_date)
    _write_completion_audit_json(
        runtime_root,
        prediction_date,
        status="COMPLETE_WITH_SHADOW_OPEN_NOISE",
        shadow_pending=1,
        shadow_open=1,
        paper_pending=0,
        paper_open=0,
    )
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    preview = text.split("Top Candidate Preview", 1)[1].split("Watchlists", 1)[0]

    assert payload["final_decision"] == "NO BET"
    assert "Preview rows are diagnostic only and are not betting recommendations unless they appear in Elite Picks/Kelly." in preview
    assert "SHADOW_ONLY" in preview
    assert "CLEAR" not in preview


def test_operator_card_no_bet_high_caution_preview_rows_are_watchlist_only(tmp_path: Path) -> None:
    prediction_date = "2026-05-15"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"

    candidate = _candidate(prediction_date)
    candidate["recommended_action"] = "CLEAR"
    candidate["qualification_reason"] = "elite_reject_context_high_caution_over"
    columns = list(candidate.keys())

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [candidate], columns=columns)
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(
            prediction_date,
            elite_count=0,
            full_market_count=1,
            run_health_status="NO_BET",
            games_count=1,
        ),
    )
    _seed_required_json(runtime_root, prediction_date)
    _write_completion_audit_json(
        runtime_root,
        prediction_date,
        status="COMPLETE_WITH_SHADOW_OPEN_NOISE",
        shadow_pending=1,
        shadow_open=1,
        paper_pending=0,
        paper_open=0,
    )
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    preview = text.split("Top Candidate Preview", 1)[1].split("Watchlists", 1)[0]

    assert payload["final_decision"] == "NO BET"
    assert "WATCHLIST_ONLY" in preview
    assert "CLEAR" not in preview

def test_operator_card_no_bet_preview_uses_high_caution_watchlist_overlay(tmp_path: Path) -> None:
    prediction_date = "2026-05-15"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"

    candidate = _candidate(prediction_date)
    candidate["recommended_action"] = "CLEAR"
    candidate["qualification_reason"] = ""
    columns = list(candidate.keys())

    watchlist_row = dict(candidate)
    watchlist_row["final_elite_rejection_reason"] = "elite_reject_context_high_caution_over"
    watchlist_columns = list(dict.fromkeys([*watchlist_row.keys(), "final_elite_rejection_reason"]))

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [candidate], columns=columns)
    _write_csv(operator / f"high_caution_over_watchlist_{prediction_date}.csv", [watchlist_row], columns=watchlist_columns)
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(
            prediction_date,
            elite_count=0,
            full_market_count=1,
            run_health_status="NO_BET",
            games_count=1,
        ),
    )
    _seed_required_json(runtime_root, prediction_date)
    _write_completion_audit_json(
        runtime_root,
        prediction_date,
        status="COMPLETE_WITH_SHADOW_OPEN_NOISE",
        shadow_pending=1,
        shadow_open=1,
        paper_pending=0,
        paper_open=0,
    )
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    preview = text.split("Top Candidate Preview", 1)[1].split("Watchlists", 1)[0]

    assert payload["final_decision"] == "NO BET"
    assert "WATCHLIST_ONLY" in preview
    assert "SHADOW_ONLY" not in preview
    assert "CLEAR" not in preview

def test_operator_card_shadow_open_noise_completion_audit_is_not_scary(tmp_path: Path) -> None:
    prediction_date = "2026-05-15"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    columns = list(_candidate(prediction_date).keys())

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(
            prediction_date,
            elite_count=0,
            full_market_count=0,
            run_health_status="NO_BET",
            games_count=0,
        ),
    )
    _seed_required_json(runtime_root, prediction_date)
    _write_completion_audit_json(
        runtime_root,
        prediction_date,
        status="COMPLETE_WITH_SHADOW_OPEN_NOISE",
        shadow_pending=3,
        shadow_open=3,
        paper_pending=2,
        paper_open=2,
    )
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")

    assert payload["final_decision"] == "NO BET"
    assert "- report_agreement_status: COMPLETE_WITH_SHADOW_OPEN_NOISE" in text
    assert "- real_pick_pending_count: 0" in text
    assert "- agreement issue count: none" in text
    assert "- recommended action: real picks closed / ignore shadow-paper open-game noise" in text
    assert "- recommended action: inspect completion audit before trusting results" not in text
    assert "- recommended action: no action needed; shadow/paper pending rows are open-game only" not in text

def test_operator_card_splits_optional_completion_warnings(tmp_path: Path) -> None:
    prediction_date = "2026-05-15"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    columns = list(_candidate(prediction_date).keys())

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(
            prediction_date,
            elite_count=0,
            full_market_count=0,
            run_health_status="NO_BET",
            games_count=0,
        ),
    )
    _seed_required_json(runtime_root, prediction_date)
    _write_completion_audit_json(
        runtime_root,
        prediction_date,
        status="COMPLETE_WITH_SHADOW_OPEN_NOISE",
        shadow_pending=3,
        shadow_open=3,
        paper_pending=2,
        paper_open=2,
        warnings=["Missing optional pending repair audit: fixture"],
    )
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")

    assert payload["final_decision"] == "NO BET"
    assert "- warning count: 1" in text
    assert "- blocking warning count: none" in text
    assert "- optional warning count: 1" in text
    assert "- recommended action: real picks closed / ignore shadow-paper open-game noise" in text

def test_operator_card_no_bet_reason_summary_explains_empty_elite_state(tmp_path: Path) -> None:
    prediction_date = "2026-05-15"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    columns = list(_candidate(prediction_date).keys())

    high_caution_row = _candidate(prediction_date, player_name="High Caution Over")
    combo_under_row = _candidate(
        prediction_date,
        player_name="Combo Under",
        market_type="player_points_rebounds",
        selection="under",
        edge=-2.5,
    )

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [high_caution_row, combo_under_row], columns=columns)
    _write_csv(operator / f"high_caution_over_watchlist_{prediction_date}.csv", [high_caution_row])
    _write_csv(operator / f"combo_under_watchlist_{prediction_date}.csv", [combo_under_row])
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(
            prediction_date,
            elite_count=0,
            full_market_count=2,
            high_caution_count=1,
            run_health_status="NO_BET",
            games_count=1,
        ),
    )
    _seed_required_json(runtime_root, prediction_date)
    _write_completion_audit_json(
        runtime_root,
        prediction_date,
        status="COMPLETE_WITH_SHADOW_OPEN_NOISE",
        shadow_pending=2,
        shadow_open=2,
        paper_pending=1,
        paper_open=1,
        warnings=["Missing optional pending repair audit: fixture"],
    )
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")

    assert payload["final_decision"] == "NO BET"
    assert "NO BET Reason Summary" in text
    assert "- No elite picks survived safety/context gates." in text
    assert "- 1 high-caution OVER candidates were watchlist-only." in text
    assert "- 1 combo UNDER candidates were watchlist-only." in text
    assert "- Completion audit is clean; shadow/paper pending rows are open-game only." in text

def test_operator_card_no_bet_elite_rejection_summary_uses_existing_payloads(tmp_path: Path) -> None:
    prediction_date = "2026-05-15"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    columns = list(_candidate(prediction_date).keys())

    high_caution_row = _candidate(prediction_date, player_name="High Caution Over")
    combo_under_row = _candidate(
        prediction_date,
        player_name="Combo Under",
        market_type="player_points_rebounds",
        selection="under",
        edge=-2.5,
    )

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [high_caution_row, combo_under_row], columns=columns)
    _write_csv(operator / f"high_caution_over_watchlist_{prediction_date}.csv", [high_caution_row])
    _write_csv(operator / f"combo_under_watchlist_{prediction_date}.csv", [combo_under_row])
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        {
            **_quality_payload(
                prediction_date,
                elite_count=0,
                full_market_count=2,
                high_caution_count=2,
                same_opponent_count=1,
                run_health_status="NO_BET",
                games_count=1,
            ),
            "unsupported_active_operator_markets": {
                "rejection_reason": "unsupported_active_operator_market",
                "total_rows_dropped": 3,
                "counts_by_market_type": {"player_blocks": 1, "player_steals": 2},
            },
        },
    )
    _seed_required_json(runtime_root, prediction_date)
    _write_json(
        diagnostics / f"board_diagnostics_{prediction_date}.json",
        {
            "board_counts": {"qualified_pool": 2, "rejected": 2},
            "top_rejection_reasons": [
                {"reason": "market_filtered_by_elite_policy", "count": 7},
                {"reason": "reject_quality_confidence_threshold", "count": 4},
            ],
            "elite_context_safety_gate": {
                "candidate_rejection_reason_counts": {
                    "elite_reject_context_high_caution_over": 2
                }
            },
        },
    )
    _write_completion_audit_json(
        runtime_root,
        prediction_date,
        status="COMPLETE_WITH_SHADOW_OPEN_NOISE",
        shadow_pending=2,
        shadow_open=2,
        paper_pending=1,
        paper_open=1,
    )
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")

    assert payload["final_decision"] == "NO BET"
    assert "Elite Rejection Summary" in text
    assert "- high-caution OVER context gate: 2" in text
    assert "- combo UNDER watchlist: 1" in text
    assert "- unsupported active markets dropped: 3" in text
    assert "- same-opponent UNDER warnings: 1" in text
    assert "- top rejection reason: market_filtered_by_elite_policy (7)" in text

def test_operator_card_audit_warning_summary_classifies_pass_with_warnings_as_non_blocking(tmp_path: Path) -> None:
    prediction_date = "2026-05-15"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    columns = list(_candidate(prediction_date).keys())

    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=columns)
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [_candidate(prediction_date)], columns=columns)
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(
            prediction_date,
            elite_count=0,
            full_market_count=1,
            run_health_status="NO_BET",
            games_count=1,
        ),
    )
    _seed_required_json(runtime_root, prediction_date)
    _write_json(
        diagnostics / f"full_market_sanity_audit_{prediction_date}.json",
        {
            "status": "PASS_WITH_WARNINGS",
            "failure_count": 0,
            "issues": [],
            "warning_count": 0,
            "recommended_action": "continue",
        },
    )
    _write_json(
        diagnostics / f"candidate_quality_drift_audit_{prediction_date}.json",
        {
            "status": "PASS_WITH_WARNINGS",
            "failure_count": 0,
            "issues": [],
            "warning_count": 0,
            "recommended_action": "continue",
        },
    )
    _write_completion_audit_json(
        runtime_root,
        prediction_date,
        status="COMPLETE_WITH_SHADOW_OPEN_NOISE",
        shadow_pending=1,
        shadow_open=1,
        paper_pending=0,
        paper_open=0,
    )
    _seed_history(history_root)

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")

    assert payload["final_decision"] == "NO BET"
    assert "Audit Warning Summary" in text
    assert "- full-market sanity audit: PASS_WITH_WARNINGS, non-blocking (blocking=none, warnings=none)" in text
    assert "- candidate quality drift audit: PASS_WITH_WARNINGS, non-blocking (blocking=none, warnings=none)" in text
    assert "- blocking audit warnings: none" in text
    assert "- operator action: continue only if final_decision rules remain clean." in text
    assert "- operator action: inspect audit before trusting results." not in text

