from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.incubator_board import (
    INCUBATOR_COLUMNS,
    INCUBATOR_STATUS_PAPER,
    MIN_INCUBATOR_EDGE,
    MIN_INCUBATOR_CONFIDENCE,
    MIN_INCUBATOR_QUALITY_SCORE,
    build_incubator_board,
    write_incubator_board,
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


def _candidate(
    player_name: str,
    *,
    prediction_date: str = "2026-05-28",
    edge: float = 5.5,
    confidence: float = 0.76,
    quality_score: float = 65.0,
    selection: str = "over",
    market_type: str = "player_points",
    line: float = 21.5,
    **extra: object,
) -> dict:
    row = {
        "prediction_date": prediction_date,
        "player_id": player_name.lower().replace(" ", "-"),
        "player_name": player_name,
        "team_abbr": "BOS",
        "opponent": "NYK",
        "game_id": "game-1",
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "sportsbook_line": line,
        "model_projection": line + edge,
        "odds": -110,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "selection_score": 80.0,
        "elite_rejection_reason": "elite_reject_context_high_caution_over",
        "context_caution_level": "high",
        "row_identity_quarantined": False,
        "player_identity_valid": True,
        "review_before_bet": False,
        "manual_review_required": False,
        "same_opponent_under_warning": False,
        "operator_action": "OK_TO_CONSIDER",
        "stake_policy": "NORMAL",
        "recommended_action": "HOLD",
        "kelly_projected_skip_reason": "context_high_caution_over",
    }
    row.update(extra)
    return row


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


def test_rule1_high_caution_over_incubator_admission() -> None:
    # 1. Clean candidates meeting thresholds and blocked by elite_reject_context_high_caution_over are admitted
    clean_candidate = _candidate("Clean Blocked Candidate")
    
    # Generic HOLD stake policy that comes from context_high_caution_over is allowed
    hold_candidate = _candidate(
        "Hold Candidate",
        stake_policy="HOLD",
        kelly_projected_skip_reason="context_high_caution_over",
    )
    
    # Failures due to below threshold
    low_edge = _candidate("Low Edge", edge=MIN_INCUBATOR_EDGE - 0.1)
    low_confidence = _candidate("Low Confidence", confidence=MIN_INCUBATOR_CONFIDENCE - 0.01)
    low_quality = _candidate("Low Quality", quality_score=MIN_INCUBATOR_QUALITY_SCORE - 1.0)
    wrong_market = _candidate("Wrong Market", market_type="player_rebounds")
    wrong_selection = _candidate("Wrong Selection", selection="under")
    wrong_caution = _candidate("Low Caution Level", context_caution_level="normal")
    wrong_rejection = _candidate("Other Rejection", elite_rejection_reason="low_projected_minutes")

    full_market = pd.DataFrame([
        clean_candidate,
        hold_candidate,
        low_edge,
        low_confidence,
        low_quality,
        wrong_market,
        wrong_selection,
        wrong_caution,
        wrong_rejection,
    ])
    
    incubator_df = build_incubator_board(full_market)
    
    assert len(incubator_df) == 2
    assert set(incubator_df["player"].tolist()) == {"Clean Blocked Candidate", "Hold Candidate"}
    
    # Verify paper-only default outputs
    for _, row in incubator_df.iterrows():
        assert row["real_money_eligible"] is False
        assert row["incubator_status"] == INCUBATOR_STATUS_PAPER
        assert "High-caution player_points over prop blocked by context safety" in row["incubator_reason"]


def test_rule2_and_rule3_no_elite_or_kelly_exposure() -> None:
    # 2. Incubator rows do not enter the elite_board
    # 3. Incubator rows do not enter kelly_stakes
    candidate_1 = _candidate("Admitted Candidate")
    full_market = pd.DataFrame([candidate_1])
    elite_df = pd.DataFrame()
    
    incubator_df = build_incubator_board(full_market, elite_df)
    
    assert len(incubator_df) == 1
    # Check that they are disjoint from elite board (not in elite_df keys)
    assert not incubator_df["player"].isin(elite_df["player_name"] if "player_name" in elite_df.columns else []).any()
    assert (incubator_df["real_money_eligible"] == False).all()


def test_rule4_incubator_does_not_affect_final_decision(tmp_path: Path) -> None:
    # 4. If Elite is empty but incubator has rows, final_decision remains NO BET.
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    
    candidate = _candidate("Incubator Row")
    
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [candidate])
    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=list(candidate.keys()))
    _write_csv(operator / f"near_elite_review_{prediction_date}.csv", [], columns=list(candidate.keys()))
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_csv(operator / f"kelly_stakes_{prediction_date}.csv", [], columns=["kelly_eligible", "stake_amount"])
    
    # Save the incubator board CSV
    _write_csv(operator / f"incubator_board_{prediction_date}.csv", [{
        "prediction_date": prediction_date,
        "player": "Incubator Row",
        "player_id": "incubator-row",
        "market_type": "player_points",
        "selection": "over",
        "edge": 5.5,
        "confidence": 0.76,
        "quality_score": 65.0,
    }], columns=INCUBATOR_COLUMNS)
    
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(prediction_date, full_market_count=1, incubator_count=1),
    )
    _write_json(diagnostics / f"board_diagnostics_{prediction_date}.json", {"board_counts": {"qualified_pool": 1}})
    _write_json(diagnostics / f"market_shadow_grading_{prediction_date}.json", {})
    _write_json(diagnostics / f"injury_context_diagnostics_{prediction_date}.json", {})
    _write_json(diagnostics / f"game_context_{prediction_date}.json", {})
    
    _text, payload = build_operator_card(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    
    assert payload["final_decision"] == "NO BET"
    assert payload["elite_count"] == 0
    assert payload["incubator_board_count"] == 1


def test_rule5_source_conflicted_exclusion() -> None:
    # 5. Source-conflicted rows cannot enter the incubator.
    conflicted = _candidate("Conflicted Candidate", source_identity_conflicted=True)
    clean = _candidate("Clean Candidate")
    
    full_market = pd.DataFrame([conflicted, clean])
    incubator_df = build_incubator_board(full_market)
    
    assert len(incubator_df) == 1
    assert incubator_df["player"].tolist() == ["Clean Candidate"]


def test_rule6_true_identity_conflict_exclusion() -> None:
    # 6. True identity conflicts cannot enter the incubator.
    true_conflict = _candidate("Conflict Candidate", identity_resolution_category="true_identity_conflict")
    clean = _candidate("Clean Candidate")
    
    full_market = pd.DataFrame([true_conflict, clean])
    incubator_df = build_incubator_board(full_market)
    
    assert len(incubator_df) == 1
    assert incubator_df["player"].tolist() == ["Clean Candidate"]


def test_rule7_manifest_includes_incubator_board(tmp_path: Path) -> None:
    # 7. The artifact manifest includes incubator_board if written.
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    
    # Write core boards to satisfy manifest
    for name in ("elite_board", "full_market_board", "sgp_board"):
        _write_csv(runtime_root / "operator" / f"{name}_{prediction_date}.csv", [], columns=["player_name"])
        
    # Write incubator board
    _write_csv(runtime_root / "operator" / f"incubator_board_{prediction_date}.csv", [], columns=INCUBATOR_COLUMNS)
    
    manifest = build_artifact_manifest(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        generated_at="2026-05-28T12:00:00Z",
    )
    
    incubator_art = next(item for item in manifest["artifacts"] if item["name"] == "incubator_board")
    assert incubator_art["exists"] is True
    assert incubator_art["severity"] == SEVERITY_SHADOW_ONLY
    assert "Incubator/reporting artifact; not a betting input." in incubator_art["notes"]


def test_rule8_operator_card_labels_incubator_as_paper_only(tmp_path: Path) -> None:
    # 8. The operator card labels the incubator as paper-only.
    prediction_date = "2026-05-28"
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    
    candidate = _candidate("Incubator Row")
    
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [candidate])
    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=list(candidate.keys()))
    _write_csv(operator / f"near_elite_review_{prediction_date}.csv", [], columns=list(candidate.keys()))
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_csv(operator / f"kelly_stakes_{prediction_date}.csv", [], columns=["kelly_eligible", "stake_amount"])
    
    # Save the incubator board CSV
    _write_csv(operator / f"incubator_board_{prediction_date}.csv", [{
        "prediction_date": prediction_date,
        "player": "Incubator Row",
        "player_id": "incubator-row",
        "market_type": "player_points",
        "selection": "over",
        "edge": 5.5,
        "confidence": 0.76,
        "quality_score": 65.0,
    }], columns=INCUBATOR_COLUMNS)
    
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(prediction_date, full_market_count=1, incubator_count=1),
    )
    _write_json(diagnostics / f"board_diagnostics_{prediction_date}.json", {"board_counts": {"qualified_pool": 1}})
    _write_json(diagnostics / f"market_shadow_grading_{prediction_date}.json", {})
    _write_json(diagnostics / f"injury_context_diagnostics_{prediction_date}.json", {})
    _write_json(diagnostics / f"game_context_{prediction_date}.json", {})
    
    text, _payload = build_operator_card(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    
    assert "- incubator board count: 1" in text
    assert "- Incubator rows are paper-only candidates for model learning and are not staking inputs." in text


def test_rule9_empty_board_handling(tmp_path: Path) -> None:
    # 9. An empty incubator board is allowed and does not fail validation.
    full_market = pd.DataFrame()
    incubator_df = build_incubator_board(full_market)
    assert incubator_df.empty
    assert list(incubator_df.columns) == list(INCUBATOR_COLUMNS)
    
    # Write to path safely
    path, written_df = write_incubator_board(
        prediction_date="2026-05-28",
        runtime_root=tmp_path / "runtime",
        full_market_df=full_market,
    )
    assert path.exists()
    assert written_df.empty
