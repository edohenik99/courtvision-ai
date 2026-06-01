from __future__ import annotations

import json
from pathlib import Path
import pandas as pd
import pytest

from scripts.pre_game_finalization_guard import run_pre_game_finalization_guard


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

    # 1. full_market_board
    _write_csv(operator / f"full_market_board_{date}.csv", [{"prediction_date": date}])

    # 2. operator_card with snapshot and disclaimers
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

    # 3. daily_summary with snapshots
    summary_text = """
UNDER Research Snapshot - Shadow Only
historical shadow UNDER
Top Current UNDER Candidates
Top UNDER_ALIGNED_RESEARCH Candidates
"""
    _write_text(operator / f"daily_summary_{date}.txt", summary_text)

    # 4. quality_summary
    _write_text(operator / f"quality_summary_{date}.txt", "Quality Summary Text")

    # 5. board_diagnostics
    _write_json(diagnostics / f"board_diagnostics_{date}.json", {"status": "ok"})


def _seed_optional_artifacts(runtime_root: Path, date: str = PREDICTION_DATE) -> None:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"

    # 1. under_visibility_audit txt & json
    _write_text(operator / f"under_visibility_audit_{date}.txt", "Audit Text")
    _write_json(diagnostics / f"under_visibility_audit_{date}.json", {
        "prediction_date": date,
        "funnel_stages": {"total": 5},
        "read_only": True,
        "betting_logic_changed": False
    })

    # 2. shadow_candidate_lane CSV
    shadow_row = {
        "prediction_date": date,
        "source_artifact_date": date,
        "real_money_eligible": False,
        "kelly_eligible": False,
        "elite_eligible": False,
        "shadow_only": True
    }
    _write_csv(operator / f"shadow_candidate_lane_{date}.csv", [shadow_row])

    # 3. shadow_candidate_lane report txt & json
    _write_text(operator / f"shadow_candidate_lane_report_{date}.txt", "Shadow lane report")
    _write_json(diagnostics / f"shadow_candidate_lane_{date}.json", {"status": "ok"})

    # 4. shadow_candidate_lane_performance txt & json
    _write_text(operator / f"shadow_candidate_lane_performance_{date}.txt", "Shadow performance report")
    _write_json(diagnostics / f"shadow_candidate_lane_performance_{date}.json", {
        "source_date_mismatch": False,
        "history_persistence_status": "persisted",
        "all_rows_real_money_eligible_false": True,
        "all_rows_kelly_eligible_false": True,
        "all_rows_elite_eligible_false": True,
        "all_rows_shadow_only_true": True
    })


def _seed_under_visibility_board_artifacts(runtime_root: Path, date: str = PREDICTION_DATE) -> None:
    """Seed Phase 6B.1 UNDER visibility board artifacts separately."""
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    _write_text(operator / f"under_visibility_board_{date}.csv", "player_name\n")
    _write_text(operator / f"under_visibility_report_{date}.txt", "UNDER visibility board report")
    _write_json(diagnostics / f"under_visibility_board_{date}.json", {
        "prediction_date": date,
        "shadow_only": True,
        "betting_logic_changed": False,
        "real_money_promotion": False,
        "elite_promotion": False,
        "kelly_promotion": False,
        "pick_history_written": False,
        "board_row_count": 0,
    })


def test_all_required_and_optional_present_and_clean_ready_to_lock(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)
    _seed_under_visibility_board_artifacts(runtime_root)

    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert exit_code == 0
    assert payload["status"] == "READY_TO_LOCK"
    assert payload["recommended_action"] == "safe to preserve artifacts and wait for game"
    assert len(payload["errors"]) == 0
    assert len(payload["warnings"]) == 0


def test_missing_required_artifact_not_ready(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Delete full_market_board
    full_market = runtime_root / "operator" / f"full_market_board_{PREDICTION_DATE}.csv"
    assert full_market.exists()
    full_market.unlink()

    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert exit_code == 2
    assert payload["status"] == "NOT_READY"
    assert payload["recommended_action"] == "rerun/fix artifacts before locking"
    assert any("full_market_board" in err for err in payload["errors"])


def test_missing_optional_research_artifact_ready_with_warnings(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    # Seed only some optional artifacts, leave others missing
    _write_text(runtime_root / "operator" / f"under_visibility_audit_{PREDICTION_DATE}.txt", "Some text")

    # Run without strict
    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )
    assert exit_code == 0
    assert payload["status"] == "READY_WITH_WARNINGS"
    assert payload["recommended_action"] == "review warnings before locking"
    assert len(payload["warnings"]) > 0
    assert len(payload["errors"]) == 0

    # Run with strict
    exit_code_strict, payload_strict, report_strict = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=True,
    )
    # Status label remains READY_WITH_WARNINGS, but exit code escalates to 1
    assert exit_code_strict == 1
    assert payload_strict["status"] == "READY_WITH_WARNINGS"


def test_shadow_candidate_lane_source_artifact_date_mismatch_not_ready(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Seed shadow lane with a mismatched source_artifact_date
    shadow_row = {
        "prediction_date": PREDICTION_DATE,
        "source_artifact_date": "2026-05-29",  # mismatch!
        "real_money_eligible": False,
        "kelly_eligible": False,
        "elite_eligible": False,
        "shadow_only": True
    }
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{PREDICTION_DATE}.csv", [shadow_row])

    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert exit_code == 2
    assert payload["status"] == "NOT_READY"
    assert any("mismatch" in err.lower() for err in payload["errors"])


def test_shadow_candidate_lane_history_contamination_not_ready(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Seed shadow lane history with contaminated rows (same prediction date but mismatched source artifact date)
    history_row = {
        "prediction_date": PREDICTION_DATE,
        "source_artifact_date": "2026-05-29"  # contamination!
    }
    _write_csv(history_root / "shadow_candidate_lane_history.csv", [history_row])

    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert exit_code == 2
    assert payload["status"] == "NOT_READY"
    assert any("contamination" in err.lower() for err in payload["errors"])


def test_operator_card_missing_under_research_snapshot_ready_with_warnings(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Re-write operator card without UNDER Research Snapshot section
    card_text = f"""
prediction_date: {PREDICTION_DATE}
final_decision: CONTINUE
It is not an Elite board.
It is not a Kelly input.
It is not a betting recommendation.
No real-money promotion is allowed.
"""
    _write_text(runtime_root / "operator" / f"operator_card_{PREDICTION_DATE}.txt", card_text)

    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert exit_code == 0
    assert payload["status"] == "READY_WITH_WARNINGS"
    assert any("snapshot" in warn.lower() for warn in payload["warnings"])


def test_shadow_rows_with_real_money_eligible_true_not_ready(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Seed shadow lane with real_money_eligible = True
    shadow_row = {
        "prediction_date": PREDICTION_DATE,
        "source_artifact_date": PREDICTION_DATE,
        "real_money_eligible": True,  # violation!
        "kelly_eligible": False,
        "elite_eligible": False,
        "shadow_only": True
    }
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{PREDICTION_DATE}.csv", [shadow_row])

    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert exit_code == 2
    assert payload["status"] == "NOT_READY"
    assert any("real_money_eligible=True" in err for err in payload["errors"])


def test_dry_no_write_behavior_pick_history_csv_untouched(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Seed pick history to make sure it exists
    pick_history_path = history_root / "pick_history.csv"
    _write_csv(pick_history_path, [{"prediction_date": PREDICTION_DATE, "pick": "over"}])
    pick_bytes_before = pick_history_path.read_bytes()

    # Track pre-existing operator card bytes to ensure it isn't overwritten
    card_path = runtime_root / "operator" / f"operator_card_{PREDICTION_DATE}.txt"
    card_bytes_before = card_path.read_bytes()

    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    # Verify output reports are written
    assert (runtime_root / "operator" / f"pre_game_finalization_guard_{PREDICTION_DATE}.txt").exists()
    assert (runtime_root / "diagnostics" / f"pre_game_finalization_guard_{PREDICTION_DATE}.json").exists()

    # Verify no other existing files are touched
    assert pick_history_path.read_bytes() == pick_bytes_before
    assert card_path.read_bytes() == card_bytes_before


def test_json_diagnostics_contain_expected_fields(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert "prediction_date" in payload
    assert "status" in payload
    assert "recommended_action" in payload
    assert "strict" in payload
    assert "exit_code" in payload
    assert "required_checklists" in payload
    assert "research_checklists" in payload
    assert "date_integrity" in payload
    assert "under_research_snapshot" in payload
    assert "betting_safety" in payload
    assert "warnings" in payload
    assert "errors" in payload


# ============================================================
# Phase 6B.1 — UNDER Visibility Board Guard Tests
# ============================================================

def test_guard_accepts_under_visibility_board_when_clean(tmp_path: Path) -> None:
    """Guard is READY_TO_LOCK when under_visibility_board JSON has clean safety flags."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Write a clean under_visibility_board JSON
    board_payload = {
        "prediction_date": PREDICTION_DATE,
        "shadow_only": True,
        "betting_logic_changed": False,
        "real_money_promotion": False,
        "elite_promotion": False,
        "kelly_promotion": False,
        "pick_history_written": False,
        "board_row_count": 5,
    }
    _write_json(
        runtime_root / "diagnostics" / f"under_visibility_board_{PREDICTION_DATE}.json",
        board_payload,
    )
    _write_text(
        runtime_root / "operator" / f"under_visibility_board_{PREDICTION_DATE}.csv",
        "player_name\nAlice\n",
    )
    _write_text(
        runtime_root / "operator" / f"under_visibility_report_{PREDICTION_DATE}.txt",
        "UNDER board report text",
    )

    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert exit_code == 0
    assert payload["status"] == "READY_TO_LOCK"
    assert len(payload["errors"]) == 0


def test_guard_warns_when_under_visibility_board_missing(tmp_path: Path) -> None:
    """Guard is READY_WITH_WARNINGS when UNDER visibility board artifacts are absent."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)
    # No under_visibility_board files written

    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    # Missing UNDER visibility board is a warning, never fatal
    assert exit_code == 0
    assert payload["status"] == "READY_WITH_WARNINGS"
    assert len(payload["errors"]) == 0
    assert len(payload["warnings"]) > 0


def test_guard_blocks_when_under_visibility_board_json_has_real_money_promotion(tmp_path: Path) -> None:
    """Guard is NOT_READY when board JSON has real_money_promotion=True."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    # Corrupt board JSON: real_money_promotion=True
    board_payload = {
        "prediction_date": PREDICTION_DATE,
        "shadow_only": True,
        "betting_logic_changed": False,
        "real_money_promotion": True,  # violation!
        "elite_promotion": False,
        "kelly_promotion": False,
        "pick_history_written": False,
    }
    _write_json(
        runtime_root / "diagnostics" / f"under_visibility_board_{PREDICTION_DATE}.json",
        board_payload,
    )

    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert exit_code == 2
    assert payload["status"] == "NOT_READY"
    assert any("real_money_promotion" in err for err in payload["errors"])


def test_guard_blocks_when_under_visibility_board_json_pick_history_written(tmp_path: Path) -> None:
    """Guard is NOT_READY when board JSON has pick_history_written=True."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    _seed_required_artifacts(runtime_root)
    _seed_optional_artifacts(runtime_root)

    board_payload = {
        "prediction_date": PREDICTION_DATE,
        "shadow_only": True,
        "betting_logic_changed": False,
        "real_money_promotion": False,
        "pick_history_written": True,  # violation!
    }
    _write_json(
        runtime_root / "diagnostics" / f"under_visibility_board_{PREDICTION_DATE}.json",
        board_payload,
    )

    exit_code, payload, report = run_pre_game_finalization_guard(
        prediction_date=PREDICTION_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
        strict=False,
    )

    assert exit_code == 2
    assert payload["status"] == "NOT_READY"
    assert any("pick_history_written" in err for err in payload["errors"])

