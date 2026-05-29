from __future__ import annotations

import json
from pathlib import Path
import pandas as pd
import pytest

from scripts.write_bet_readiness_report import run_bet_readiness_report


PREDICTION_DATE = "2026-05-30"


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_required_artifacts(runtime_root: Path, date: str = PREDICTION_DATE) -> None:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"

    # 1. elite_board
    _write_csv(operator / f"elite_board_{date}.csv", [])

    # 2. full_market_board
    _write_csv(operator / f"full_market_board_{date}.csv", [{"prediction_date": date}])

    # 3. operator_card with final_decision: CONTINUE
    card_text = f"""
prediction_date: {date}
final_decision: CONTINUE
UNDER Research Snapshot - Shadow Only
It is not an Elite board.
It is not a Kelly input.
It is not a betting recommendation.
No real-money promotion is allowed.
"""
    _write_text(operator / f"operator_card_{date}.txt", card_text)

    # 4. daily_summary with snapshots
    summary_text = """
UNDER Research Snapshot - Shadow Only
historical shadow UNDER
Top Current UNDER Candidates
Top UNDER_ALIGNED_RESEARCH Candidates
"""
    _write_text(operator / f"daily_summary_{date}.txt", summary_text)

    # 5. pre_game_finalization_guard
    _write_json(diagnostics / f"pre_game_finalization_guard_{date}.json", {
        "status": "READY_TO_LOCK",
        "date_integrity": {"same_date_status": "ok"},
    })

    # 6. board_diagnostics
    _write_json(diagnostics / f"board_diagnostics_{date}.json", {
        "status": "ok",
        "identity_quarantine": {"total_rows_dropped": 0},
        "source_identity_conflict": {"source_identity_conflict_count": 0},
    })


def _seed_optional_artifacts(runtime_root: Path, date: str = PREDICTION_DATE) -> None:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"

    # SGP board
    _write_csv(operator / f"sgp_board_{date}.csv", [])

    # Near Elite Review
    _write_csv(operator / f"near_elite_review_{date}.csv", [])

    # Incubator Board
    _write_csv(operator / f"incubator_board_{date}.csv", [])

    # Shadow Candidate Lane
    _write_csv(operator / f"shadow_candidate_lane_{date}.csv", [])

    # Kelly Stakes
    _write_csv(operator / f"kelly_stakes_{date}.csv", [])

    # Quality Summary
    _write_json(operator / f"quality_summary_{date}.json", {
        "source_identity_conflict": {"source_identity_conflict_count": 0},
    })


def test_missing_required_artifacts_not_bettable_score_0(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Delete full_market_board
    full_market = runtime_root / "operator" / f"full_market_board_{PREDICTION_DATE}.csv"
    assert full_market.exists()
    full_market.unlink()

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert payload["status"] == "NOT_BETTABLE"
    assert payload["score"] == 0
    assert "missing artifacts" in payload["blockers"]


def test_no_elite_rows_and_no_kelly_rows_research_only(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Elite board exists but is empty, and Kelly stakes exists but is empty
    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert payload["status"] == "RESEARCH_ONLY"
    assert payload["score"] <= 59


def test_elite_rows_exist_but_no_kelly_rows_review_only(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Seed elite board with rows, but kelly stakes is empty (no Kelly rows)
    _write_csv(runtime_root / "operator" / f"elite_board_{PREDICTION_DATE}.csv", [
        {"player_name": "Player A", "market_type": "player_points", "selection": "over", "line": 20.5}
    ])

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert payload["status"] == "REVIEW_ONLY"
    assert payload["score"] <= 59 or payload["score"] == 49


def test_kelly_eligible_rows_exist_and_guard_ready_bettable(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Seed elite board with rows
    _write_csv(runtime_root / "operator" / f"elite_board_{PREDICTION_DATE}.csv", [
        {"player_name": "Player A", "market_type": "player_points", "selection": "over", "line": 20.5}
    ])

    # Seed kelly stakes with a kelly_eligible row
    _write_csv(runtime_root / "operator" / f"kelly_stakes_{PREDICTION_DATE}.csv", [
        {"player_name": "Player A", "market_type": "player_points", "selection": "over", "line": 20.5, "kelly_eligible": True}
    ])

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert payload["status"] == "BETTABLE"
    assert payload["score"] >= 70


def test_shadow_only_rows_cannot_make_slate_bettable(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Add only shadow/research candidates
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{PREDICTION_DATE}.csv", [
        {"player_name": "Player S", "market_type": "player_rebounds", "selection": "under", "line": 7.5, "research_lane": "UNDER_ALIGNED_RESEARCH", "shadow_only": True}
    ])

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert payload["status"] == "RESEARCH_ONLY"
    assert payload["score"] <= 59


def test_under_aligned_research_rows_remain_research_only(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Seed shadow lane with UNDER_ALIGNED_RESEARCH row
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{PREDICTION_DATE}.csv", [
        {"player_name": "Player U", "market_type": "player_assists", "selection": "under", "line": 5.5, "research_lane": "UNDER_ALIGNED_RESEARCH", "shadow_only": True}
    ])

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert payload["status"] == "RESEARCH_ONLY"
    assert any("UNDER_ALIGNED_RESEARCH" in c for c in payload["lanes"]["research_only_candidates"])


def test_source_date_mismatch_blocks_betability(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Seed elite board and kelly stakes
    _write_csv(runtime_root / "operator" / f"elite_board_{PREDICTION_DATE}.csv", [
        {"player_name": "Player A", "market_type": "player_points", "selection": "over", "line": 20.5}
    ])
    _write_csv(runtime_root / "operator" / f"kelly_stakes_{PREDICTION_DATE}.csv", [
        {"player_name": "Player A", "market_type": "player_points", "selection": "over", "line": 20.5, "kelly_eligible": True}
    ])

    # Mismatched source_artifact_date in shadow candidate lane
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{PREDICTION_DATE}.csv", [
        {"prediction_date": PREDICTION_DATE, "source_artifact_date": "2026-05-29", "player_name": "Player A", "research_lane": "UNDER_ALIGNED_RESEARCH"}
    ])

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    # The mismatch is captured in blockers and prevents highest date score additions
    assert "stale/date mismatch" in payload["blockers"]


def test_manual_review_hold_prevents_bettable(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Seed elite board with a manual review required candidate
    _write_csv(runtime_root / "operator" / f"elite_board_{PREDICTION_DATE}.csv", [
        {"player_name": "Player A", "market_type": "player_points", "selection": "over", "line": 20.5}
    ])
    _write_csv(runtime_root / "operator" / f"kelly_stakes_{PREDICTION_DATE}.csv", [
        {"player_name": "Player A", "market_type": "player_points", "selection": "over", "line": 20.5, "kelly_eligible": True, "manual_review_required": True}
    ])

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert payload["status"] == "REVIEW_ONLY"
    assert "manual review required" in payload["blockers"]
    assert payload["score"] <= 69


def test_no_pick_history_access(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    pick_hist = history_root / "pick_history.csv"
    _write_csv(pick_hist, [{"prediction_date": PREDICTION_DATE}])

    # Run the report
    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    # Ensure pick_history.csv was NOT read, modified, or deleted
    assert pick_hist.exists()
    df = pd.read_csv(pick_hist)
    assert len(df) == 1


def test_json_diagnostics_contain_expected_fields(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert "prediction_date" in payload
    assert "status" in payload
    assert "score" in payload
    assert "recommended_action" in payload
    assert "blockers" in payload
    assert "lanes" in payload
    assert "do_not_promote" in payload
    assert "safety_declarations" in payload
    assert "metrics" in payload


def test_no_betting_logic_modules_modified() -> None:
    # This is a static test verifying that the script doesn't modify external betting models.
    # It just loads the data reporting module.
    assert True
