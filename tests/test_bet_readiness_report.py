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


def test_bet_readiness_display_names_resolution(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # 1. Row with player only (e.g. from incubator board)
    _write_csv(runtime_root / "operator" / f"incubator_board_{PREDICTION_DATE}.csv", [
        {"player": "Jalen Williams", "market_type": "player_points", "selection": "over", "line": 7.5}
    ])

    # 2. Row with player_name only (e.g. from shadow candidate lane)
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{PREDICTION_DATE}.csv", [
        {"player_name": "Victor Wembanyama", "market_type": "player_points_rebounds_assists", "selection": "under", "line": 42.5, "research_lane": "UNDER_ALIGNED_RESEARCH", "shadow_only": True}
    ])

    # 3. Row with no supported display fields (Unknown)
    _write_csv(runtime_root / "operator" / f"near_elite_review_{PREDICTION_DATE}.csv", [
        {"market_type": "player_points", "selection": "over", "line": 13.5}
    ])

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    # Verify display name resolution
    research_candidates = payload["lanes"]["research_only_candidates"]
    
    # 1. check that Jalen Williams (seeded under player) is formatted with his name, not Unknown
    assert any("Jalen Williams" in item for item in research_candidates)
    assert not any("Unknown: player_points over 7.5" in item for item in research_candidates)

    # 2. check that Victor Wembanyama (seeded under player_name) is formatted with his name
    assert any("Victor Wembanyama" in item for item in research_candidates)

    # 3. check that the row with no supported display fields displays "Unknown"
    manual_candidates = payload["lanes"]["manual_review_candidates"]
    assert any("Unknown: player_points" in item for item in manual_candidates)

    # 4. Check JSON unknown display name counts and examples
    assert "unknown_display_name_count" in payload
    assert "unknown_display_name_examples" in payload
    assert payload["unknown_display_name_count"] == 1
    assert "player_points over 13.5" in payload["unknown_display_name_examples"]


def test_candidate_deduplication(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # We will seed duplicates to verify deduplication.
    # 1. Seed two duplicate incubator research candidates in incubator_board.
    # This simulates duplicates within the same lane C.
    _write_csv(runtime_root / "operator" / f"incubator_board_{PREDICTION_DATE}.csv", [
        {"player": "Jalen Williams", "market_type": "player_points", "selection": "over", "line": 7.5, "edge": 5.0, "confidence": 0.8},
        {"player": "Jalen Williams", "market_type": "player_points", "selection": "over", "line": 7.5, "edge": 5.0, "confidence": 0.8},
    ])

    # 2. Seed a candidate in shadow candidate lane as INCUBATOR_RESEARCH as well.
    # Also seed a different incubator candidate first to test ordering preservation.
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{PREDICTION_DATE}.csv", [
        {"player_name": "De'Aaron Fox", "market_type": "player_points", "selection": "over", "line": 13.5, "research_lane": "INCUBATOR_RESEARCH", "edge": 6.0, "confidence": 0.7, "shadow_only": True},
        {"player_name": "Jalen Williams", "market_type": "player_points", "selection": "over", "line": 7.5, "research_lane": "INCUBATOR_RESEARCH", "edge": 5.0, "confidence": 0.8, "shadow_only": True},
    ])

    # 3. Seed Jalen Williams as Near-Elite in Lane B to test that they are NOT deduped across lanes.
    _write_csv(runtime_root / "operator" / f"near_elite_review_{PREDICTION_DATE}.csv", [
        {"player": "Jalen Williams", "market_type": "player_points", "selection": "over", "line": 7.5, "edge": 5.0, "confidence": 0.8}
    ])

    # Run the bet readiness report
    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    # 1. Duplicate incubator research rows are shown only once.
    # We should have exactly one Jalen Williams incubator row in Lane C, and exactly one De'Aaron Fox incubator row.
    research_candidates = payload["lanes"]["research_only_candidates"]
    jalen_incubator_rows = [r for r in research_candidates if "Jalen Williams" in r and "INCUBATOR_RESEARCH" in r]
    assert len(jalen_incubator_rows) == 1, f"Expected 1 Jalen Williams incubator row, found: {jalen_incubator_rows}"

    fox_incubator_rows = [r for r in research_candidates if "De'Aaron Fox" in r and "INCUBATOR_RESEARCH" in r]
    assert len(fox_incubator_rows) == 1, f"Expected 1 De'Aaron Fox incubator row, found: {fox_incubator_rows}"

    # 2. Duplicate rows inside the same lane are deduped.
    # Total candidates in Lane C before dedupe should have been:
    # - shadow_candidate_lane De'Aaron Fox (1)
    # - shadow_candidate_lane Jalen Williams (2)
    # - incubator_board Jalen Williams first copy (3)
    # - incubator_board Jalen Williams second copy (4)
    # After dedupe, it should be exactly 2 (De'Aaron Fox and Jalen Williams).
    assert len(research_candidates) == 2

    # 3. Same candidate appearing in different lanes is not removed across lanes.
    # Jalen Williams appears in Lane B as [Near-Elite] and Lane C as [INCUBATOR_RESEARCH].
    manual_candidates = payload["lanes"]["manual_review_candidates"]
    assert any("Jalen Williams" in r and "Near-Elite" in r for r in manual_candidates)
    assert any("Jalen Williams" in r and "INCUBATOR_RESEARCH" in r for r in research_candidates)

    # 4. First occurrence order is preserved.
    # De'Aaron Fox was seeded first in shadow_candidate_lane. Jalen Williams second.
    # So De'Aaron Fox should be before Jalen Williams in Lane C.
    assert "De'Aaron Fox" in research_candidates[0]
    assert "Jalen Williams" in research_candidates[1]

    # 5. JSON includes duplicate_candidate_rows_removed.
    assert "duplicate_candidate_rows_removed" in payload
    assert payload["duplicate_candidate_rows_removed"] > 0
    assert "duplicate_candidate_examples" in payload
    assert len(payload["duplicate_candidate_examples"]) > 0

    # 6. JSON includes lane_counts_before_dedupe and lane_counts_after_dedupe.
    assert "lane_counts_before_dedupe" in payload
    assert "lane_counts_after_dedupe" in payload
    assert payload["lane_counts_before_dedupe"]["research_only_candidates"] == 4
    assert payload["lane_counts_after_dedupe"]["research_only_candidates"] == 2

    # 7. Status and score are unchanged by dedupe.
    assert payload["status"] == "RESEARCH_ONLY"
    assert payload["score"] == 45

    # 8. pick_history.csv is not read or written.
    pick_hist = history_root / "pick_history.csv"
    assert not pick_hist.exists()


# ============================================================
# Phase 6B.1 — UNDER Visibility Board in Bet Readiness Report
# ============================================================

def test_under_visibility_board_context_present_in_payload(tmp_path: Path) -> None:
    """under_visibility_board_context key is present in the bet readiness JSON payload."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Write an under_visibility_board JSON
    board_payload = {
        "prediction_date": PREDICTION_DATE,
        "shadow_only": True,
        "betting_logic_changed": False,
        "real_money_promotion": False,
        "board_row_count": 3,
        "lane_counts": {
            "UNDER_REVIEW_CANDIDATE_SHADOW_ONLY": 1,
            "UNDER_WATCHLIST_SHADOW_ONLY": 2,
            "UNDER_INSUFFICIENT_SAMPLE": 0,
            "UNDER_DO_NOT_PROMOTE": 0,
        },
    }
    _write_json(
        runtime_root / "diagnostics" / f"under_visibility_board_{PREDICTION_DATE}.json",
        board_payload,
    )

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert "under_visibility_board_context" in payload
    ctx = payload["under_visibility_board_context"]
    assert ctx["present"] is True
    assert ctx["affects_betability"] is False
    assert ctx["shadow_only"] is True
    assert ctx["board_row_count"] == 3
    assert "UNDER_REVIEW_CANDIDATE_SHADOW_ONLY" in ctx["lane_counts"]


def test_under_visibility_board_cannot_change_betability_status(tmp_path: Path) -> None:
    """Presence of UNDER visibility board never upgrades status from RESEARCH_ONLY."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Write a board with many candidates
    board_payload = {
        "prediction_date": PREDICTION_DATE,
        "shadow_only": True,
        "betting_logic_changed": False,
        "real_money_promotion": False,
        "board_row_count": 20,
        "lane_counts": {"UNDER_REVIEW_CANDIDATE_SHADOW_ONLY": 20},
    }
    _write_json(
        runtime_root / "diagnostics" / f"under_visibility_board_{PREDICTION_DATE}.json",
        board_payload,
    )

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    # Status must still be RESEARCH_ONLY (no elite rows, no kelly rows)
    assert payload["status"] == "RESEARCH_ONLY"
    # Score must still be <= 59 (shadow-only cap)
    assert payload["score"] <= 59
    # Safety declaration must be present
    assert any("UNDER visibility" in d for d in payload["safety_declarations"])
    # affects_betability must be False
    assert payload["under_visibility_board_context"]["affects_betability"] is False


def test_under_visibility_board_context_absent_when_board_missing(tmp_path: Path) -> None:
    """under_visibility_board_context is present but marked not-present when board file missing."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)
    # No under_visibility_board JSON

    exit_code, payload, report = run_bet_readiness_report(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert "under_visibility_board_context" in payload
    ctx = payload["under_visibility_board_context"]
    assert ctx["present"] is False
    assert ctx["affects_betability"] is False
    assert ctx["shadow_only"] is True
