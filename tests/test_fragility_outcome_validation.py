"""Tests for fragility/survivability outcome validation report and history preservation."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.fragility_outcome_validation import (
    VALIDATION_REPORT_VERSION,
    build_fragility_outcome_validation,
    validation_json_path_for_date,
    validation_txt_path_for_date,
    write_fragility_outcome_validation,
)
from scripts.history_tracking import (
    MARKET_SHADOW_HISTORY_COLUMNS,
    PICK_HISTORY_COLUMNS,
    PICK_HISTORY_GRADED_STATUSES,
    PICK_HISTORY_PRESERVED_GRADED_COLUMNS,
    _normalize_market_shadow_rows,
    _preserve_existing_market_shadow_grades,
    _preserve_existing_pick_grades,
    persist_daily_picks,
    persist_market_shadow_history,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _graded_history(n_hit: int = 40, n_miss: int = 30) -> pd.DataFrame:
    rows = []
    for i in range(n_hit):
        rows.append({
            "prediction_date": "2026-04-01",
            "result_status": "hit",
            "fragility_bucket": "LOW" if i % 3 != 0 else "HIGH",
            "survivability_bucket": "HIGH" if i % 3 != 0 else "LOW",
            "fragility_score": 20.0 if i % 3 != 0 else 75.0,
            "survivability_score": 80.0 if i % 3 != 0 else 25.0,
            "confidence": 0.72,
            "quality_score": 74.0,
            "edge": 1.5,
            "market_type": "player_points",
            "selection": "over",
            "shadow_roi": 0.909,
        })
    for i in range(n_miss):
        rows.append({
            "prediction_date": "2026-04-01",
            "result_status": "miss",
            "fragility_bucket": "HIGH" if i % 3 != 0 else "LOW",
            "survivability_bucket": "LOW" if i % 3 != 0 else "HIGH",
            "fragility_score": 75.0 if i % 3 != 0 else 20.0,
            "survivability_score": 25.0 if i % 3 != 0 else 80.0,
            "confidence": 0.52,
            "quality_score": 49.0,
            "edge": -0.8,
            "market_type": "player_rebounds",
            "selection": "under",
            "shadow_roi": -1.0,
        })
    return pd.DataFrame(rows)


def _pick_row(
    player: str,
    *,
    prediction_date: str = "2026-04-01",
    market: str = "player_points",
    selection: str = "over",
    line: float = 20.5,
    result_status: str = "pending",
    actual_value: str = "",
    fragility_bucket: str = "",
    fragility_score: str = "",
    survivability_bucket: str = "",
    survivability_score: str = "",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "run_timestamp": f"{prediction_date}T12:00:00+00:00",
        "player_name": player,
        "player_id": "",
        "team": "BOS",
        "opponent": "NYK",
        "game_id": "",
        "market": market,
        "selection": selection,
        "line": line,
        "projection": 22.0,
        "edge": 1.5,
        "abs_edge": 1.5,
        "odds": "-110",
        "confidence": "0.75",
        "quality_score": 80.0,
        "qualification_reason": "pass",
        "provider_used": "test",
        "result_status": result_status,
        "actual_value": actual_value,
        "grading_skip_reason": "",
        "kelly_eligible": "",
        "skip_reason": "",
        "context_caution_level": "",
        "context_pick_alignment": "",
        "line_source": "",
        "fragility_score": fragility_score,
        "fragility_bucket": fragility_bucket,
        "fragility_reasons": "",
        "survivability_score": survivability_score,
        "survivability_bucket": survivability_bucket,
        "survivability_reasons": "",
    }


# ── build_fragility_outcome_validation ────────────────────────────────────────

class TestBuildFragilityOutcomeValidation:
    def test_empty_history_does_not_crash(self) -> None:
        payload = build_fragility_outcome_validation(pd.DataFrame(), "2026-05-01")
        assert payload["total_graded_rows"] == 0
        assert payload["global_hit_rate"] is None
        assert payload["by_fragility_bucket"] == []
        assert "operator_verdict" in payload

    def test_missing_fragility_columns_does_not_crash(self) -> None:
        df = pd.DataFrame([
            {"result_status": "hit", "edge": 1.0},
            {"result_status": "miss", "edge": -0.5},
        ])
        payload = build_fragility_outcome_validation(df, "2026-05-01")
        assert payload["total_graded_rows"] == 2
        assert payload["by_fragility_bucket"] == []
        assert payload["by_survivability_bucket"] == []

    def test_no_graded_rows_does_not_crash(self) -> None:
        df = pd.DataFrame([
            {"result_status": "pending", "fragility_bucket": "HIGH"},
            {"result_status": None, "fragility_bucket": "LOW"},
        ])
        payload = build_fragility_outcome_validation(df, "2026-05-01")
        assert payload["total_graded_rows"] == 0
        assert payload["global_hit_rate"] is None

    def test_hit_rate_computed_correctly(self) -> None:
        df = pd.DataFrame([
            {"result_status": "hit", "fragility_bucket": "LOW", "survivability_bucket": "HIGH"},
            {"result_status": "hit", "fragility_bucket": "LOW", "survivability_bucket": "HIGH"},
            {"result_status": "miss", "fragility_bucket": "HIGH", "survivability_bucket": "LOW"},
            {"result_status": "miss", "fragility_bucket": "HIGH", "survivability_bucket": "LOW"},
        ])
        payload = build_fragility_outcome_validation(df, "2026-05-01")
        assert payload["global_hit_rate"] == pytest.approx(0.5, abs=0.001)
        assert payload["total_graded_rows"] == 4

    def test_fragility_bucket_breakdown_populated(self) -> None:
        df = _graded_history()
        payload = build_fragility_outcome_validation(df, "2026-05-01")
        buckets = {row["fragility_bucket"] for row in payload["by_fragility_bucket"]}
        assert "LOW" in buckets or "HIGH" in buckets

    def test_survivability_bucket_breakdown_populated(self) -> None:
        df = _graded_history()
        payload = build_fragility_outcome_validation(df, "2026-05-01")
        assert payload["by_survivability_bucket"]

    def test_roi_computed_from_shadow_roi(self) -> None:
        df = pd.DataFrame([
            {"result_status": "hit", "fragility_bucket": "LOW", "shadow_roi": 0.909},
            {"result_status": "miss", "fragility_bucket": "HIGH", "shadow_roi": -1.0},
        ])
        payload = build_fragility_outcome_validation(df, "2026-05-01")
        # LOW bucket: 1 hit with roi 0.909
        low_row = next(
            (r for r in payload["by_fragility_bucket"] if r.get("fragility_bucket") == "LOW"),
            None,
        )
        assert low_row is not None
        assert low_row["roi"] == pytest.approx(0.909, abs=0.001)

    def test_push_counted_not_in_hit_rate_denominator(self) -> None:
        df = pd.DataFrame([
            {"result_status": "hit", "fragility_bucket": "LOW"},
            {"result_status": "push", "fragility_bucket": "LOW"},
            {"result_status": "miss", "fragility_bucket": "LOW"},
        ])
        payload = build_fragility_outcome_validation(df, "2026-05-01")
        # hit_rate should be 1/(1+1) = 0.5 (push excluded from denominator)
        assert payload["global_hit_rate"] == pytest.approx(0.5, abs=0.001)
        low_row = payload["by_fragility_bucket"][0]
        assert low_row["pushes"] == 1

    def test_report_version_present(self) -> None:
        payload = build_fragility_outcome_validation(pd.DataFrame(), "2026-05-01")
        assert payload["report_version"] == VALIDATION_REPORT_VERSION

    def test_notes_are_diagnostic_only(self) -> None:
        payload = build_fragility_outcome_validation(_graded_history(), "2026-05-01")
        notes_str = str(payload.get("notes", []))
        assert "diagnostics_only" in notes_str or "no_betting_logic_changed" in notes_str


# ── write_fragility_outcome_validation ───────────────────────────────────────

class TestWriteFragilityOutcomeValidation:
    def test_creates_json_and_txt(self, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        df = _graded_history()
        json_path, txt_path, payload = write_fragility_outcome_validation(df, "2026-05-01", runtime)
        assert json_path.exists()
        assert txt_path.exists()
        loaded = json.loads(json_path.read_text(encoding="utf-8"))
        assert "total_graded_rows" in loaded
        assert "global_hit_rate" in loaded

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        runtime = tmp_path / "deep" / "nested" / "runtime"
        json_path, txt_path, _ = write_fragility_outcome_validation(
            pd.DataFrame(), "2026-05-09", runtime
        )
        assert json_path.exists()
        assert txt_path.exists()

    def test_txt_contains_hit_rate(self, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        df = _graded_history()
        _, txt_path, _ = write_fragility_outcome_validation(df, "2026-05-01", runtime)
        content = txt_path.read_text(encoding="utf-8")
        assert "hit rate" in content.lower() or "hit_rate" in content.lower()

    def test_path_helpers_correct(self) -> None:
        p = validation_json_path_for_date("2026-05-01", "outputs/runtime")
        assert p.name == "fragility_outcome_validation_2026-05-01.json"
        assert "diagnostics" in p.parts

        t = validation_txt_path_for_date("2026-05-01", "outputs/runtime")
        assert t.name == "fragility_outcome_validation_2026-05-01.txt"
        assert "operator" in t.parts

    def test_empty_df_does_not_crash(self, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        json_path, _, payload = write_fragility_outcome_validation(
            pd.DataFrame(), "2026-05-01", runtime
        )
        assert json_path.exists()
        assert payload["total_graded_rows"] == 0


# ── fragility/survivability survive history writes ────────────────────────────

class TestFragilitySurvivesHistoryWrites:
    def test_normalize_market_shadow_rows_includes_fragility(self) -> None:
        df = pd.DataFrame([{
            "player_name": "Alice",
            "player_id": "1",
            "team_abbr": "BOS",
            "opponent": "NYK",
            "market_type": "player_points",
            "selection": "over",
            "line": 20.5,
            "model_projection": 22.0,
            "edge": 1.5,
            "confidence": 0.72,
            "quality_score": 74.0,
            "selection_score": 0.8,
            "odds": "-110",
            "line_source": "live",
            "context_pick_alignment": "aligned",
            "context_caution_level": "low",
            "context_conflict_cause": "",
            "kelly_projected_skip_reason": "",
            "final_elite_rejection_reason": "",
            "fragility_score": 25.0,
            "fragility_bucket": "LOW",
            "fragility_reasons": "low_confidence",
            "survivability_score": 75.0,
            "survivability_bucket": "HIGH",
            "survivability_reasons": "high_confidence",
        }])
        normalized = _normalize_market_shadow_rows(df, prediction_date="2026-05-01")
        assert "fragility_score" in normalized.columns
        assert "fragility_bucket" in normalized.columns
        assert "survivability_score" in normalized.columns
        assert "survivability_bucket" in normalized.columns
        row = normalized.iloc[0]
        assert str(row["fragility_bucket"]) == "LOW"
        assert str(row["survivability_bucket"]) == "HIGH"
        assert float(row["fragility_score"]) == pytest.approx(25.0)

    def test_normalize_market_shadow_rows_no_fragility_columns(self) -> None:
        df = pd.DataFrame([{
            "player_name": "Bob",
            "player_id": "2",
            "team_abbr": "LAL",
            "opponent": "GSW",
            "market_type": "player_rebounds",
            "selection": "under",
            "line": 8.5,
            "model_projection": 7.0,
            "edge": -0.5,
            "confidence": 0.6,
            "quality_score": 65.0,
        }])
        normalized = _normalize_market_shadow_rows(df, prediction_date="2026-05-01")
        # Should not crash; fragility columns present but empty
        assert "fragility_bucket" in normalized.columns
        row = normalized.iloc[0]
        assert str(row["fragility_bucket"]) in {"", "nan", "None", ""}

    def test_persist_market_shadow_history_writes_fragility(self, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        history = tmp_path / "history"
        runtime.mkdir(parents=True)
        history.mkdir(parents=True)
        operator = runtime / "operator"
        operator.mkdir()

        date = "2026-05-01"
        board = pd.DataFrame([{
            "player_name": "Alice",
            "player_id": "1",
            "team_abbr": "BOS",
            "opponent": "NYK",
            "market_type": "player_points",
            "selection": "over",
            "line": 20.5,
            "model_projection": 22.0,
            "edge": 1.5,
            "confidence": 0.72,
            "quality_score": 74.0,
            "selection_score": 0.8,
            "odds": "-110",
            "line_source": "live",
            "context_pick_alignment": "aligned",
            "context_caution_level": "low",
            "context_conflict_cause": "",
            "kelly_projected_skip_reason": "",
            "final_elite_rejection_reason": "",
            "fragility_score": 22.5,
            "fragility_bucket": "LOW",
            "fragility_reasons": "low_risk",
            "survivability_score": 78.0,
            "survivability_bucket": "HIGH",
            "survivability_reasons": "high_conf",
        }])
        board.to_csv(operator / f"full_market_board_{date}.csv", index=False)

        persist_market_shadow_history(date, runtime_root=runtime, history_root=history)

        history_df = pd.read_csv(history / "market_shadow_history.csv")
        assert "fragility_bucket" in history_df.columns
        assert "survivability_bucket" in history_df.columns
        row = history_df[history_df["player_name"] == "Alice"].iloc[0]
        assert str(row["fragility_bucket"]) == "LOW"
        assert str(row["survivability_bucket"]) == "HIGH"


# ── graded fields not overwritten ────────────────────────────────────────────

class TestGradedFieldsNotOverwritten:
    def test_preserve_existing_pick_grades_restores_graded_row(self) -> None:
        existing = pd.DataFrame([
            _pick_row("Alice", result_status="hit", actual_value="22.0", fragility_bucket="LOW")
        ])
        incoming = pd.DataFrame([
            _pick_row("Alice", result_status="pending")
        ])
        result = _preserve_existing_pick_grades(incoming, existing)
        assert result.iloc[0]["result_status"] == "hit"
        assert str(result.iloc[0]["actual_value"]) == "22.0"
        assert str(result.iloc[0]["fragility_bucket"]) == "LOW"

    def test_preserve_existing_pick_grades_does_not_touch_new_players(self) -> None:
        existing = pd.DataFrame([
            _pick_row("Alice", result_status="hit", actual_value="22.0")
        ])
        incoming = pd.DataFrame([
            _pick_row("Bob", result_status="pending"),
        ])
        result = _preserve_existing_pick_grades(incoming, existing)
        # Bob is new — should remain pending
        assert result.iloc[0]["result_status"] == "pending"

    def test_preserve_existing_pick_grades_empty_existing(self) -> None:
        incoming = pd.DataFrame([_pick_row("Alice")])
        result = _preserve_existing_pick_grades(incoming, pd.DataFrame())
        assert len(result) == 1
        assert result.iloc[0]["result_status"] == "pending"

    def test_preserve_existing_pick_grades_empty_incoming(self) -> None:
        existing = pd.DataFrame([_pick_row("Alice", result_status="hit")])
        result = _preserve_existing_pick_grades(pd.DataFrame(), existing)
        assert result.empty

    def test_preserve_existing_pick_grades_pending_existing_not_preserved(self) -> None:
        existing = pd.DataFrame([
            _pick_row("Alice", result_status="pending")
        ])
        incoming = pd.DataFrame([
            _pick_row("Alice", result_status="pending")
        ])
        result = _preserve_existing_pick_grades(incoming, existing)
        # Pending existing should not override the incoming (no preservation trigger)
        assert result.iloc[0]["result_status"] == "pending"

    def test_preserve_existing_pick_grades_fragility_preserved(self) -> None:
        existing = pd.DataFrame([
            _pick_row(
                "Alice",
                result_status="hit",
                actual_value="22.0",
                fragility_bucket="LOW",
                fragility_score="20.0",
                survivability_bucket="HIGH",
                survivability_score="80.0",
            )
        ])
        incoming = pd.DataFrame([
            _pick_row("Alice", result_status="pending")
        ])
        result = _preserve_existing_pick_grades(incoming, existing)
        assert str(result.iloc[0]["fragility_bucket"]) == "LOW"
        assert str(result.iloc[0]["survivability_bucket"]) == "HIGH"

    def test_market_shadow_graded_fields_not_overwritten(self, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        history = tmp_path / "history"
        operator = runtime / "operator"
        operator.mkdir(parents=True)
        history.mkdir(parents=True)

        date = "2026-05-01"

        # Seed history with a graded row
        graded_row = {
            "prediction_date": date,
            "player_name": "Alice",
            "player_id": "1",
            "team_abbr": "BOS",
            "opponent": "NYK",
            "market_type": "player_points",
            "selection": "over",
            "line": 20.5,
            "model_projection": 22.0,
            "edge": 1.5,
            "confidence": 0.72,
            "quality_score": 74.0,
            "selection_score": 0.8,
            "odds": "-110",
            "line_source": "live",
            "context_pick_alignment": "aligned",
            "context_caution_level": "low",
            "context_conflict_cause": "",
            "kelly_projected_skip_reason": "",
            "final_elite_rejection_reason": "",
            "result_status": "hit",
            "actual_value": 23.0,
            "hit": True,
            "miss": False,
            "push": False,
            "shadow_roi": 0.909,
            "calibration_eligible": True,
            "calibration_exclusion_reason": "",
            "fragility_score": 22.5,
            "fragility_bucket": "LOW",
            "fragility_reasons": "",
            "survivability_score": 78.0,
            "survivability_bucket": "HIGH",
            "survivability_reasons": "",
        }
        pd.DataFrame([graded_row]).to_csv(history / "market_shadow_history.csv", index=False)

        # Re-persist same date with updated (pending) board that has fragility
        board = pd.DataFrame([{
            "player_name": "Alice",
            "player_id": "1",
            "team_abbr": "BOS",
            "opponent": "NYK",
            "market_type": "player_points",
            "selection": "over",
            "line": 20.5,
            "model_projection": 22.0,
            "edge": 1.5,
            "confidence": 0.72,
            "quality_score": 74.0,
            "selection_score": 0.8,
            "odds": "-110",
            "line_source": "live",
            "context_pick_alignment": "aligned",
            "context_caution_level": "low",
            "context_conflict_cause": "",
            "kelly_projected_skip_reason": "",
            "final_elite_rejection_reason": "",
            "fragility_score": 22.5,
            "fragility_bucket": "LOW",
            "fragility_reasons": "",
            "survivability_score": 78.0,
            "survivability_bucket": "HIGH",
            "survivability_reasons": "",
        }])
        board.to_csv(operator / f"full_market_board_{date}.csv", index=False)

        persist_market_shadow_history(date, runtime_root=runtime, history_root=history)

        refreshed = pd.read_csv(history / "market_shadow_history.csv")
        alice = refreshed[refreshed["player_name"] == "Alice"].iloc[0]

        # Graded result must be preserved
        assert str(alice["result_status"]) == "hit"
        assert float(alice["actual_value"]) == pytest.approx(23.0)
        # Fragility must be preserved (incoming also has it, so either way)
        assert str(alice["fragility_bucket"]) == "LOW"
        assert str(alice["survivability_bucket"]) == "HIGH"

    def test_persist_daily_picks_preserves_graded_row(self, tmp_path: Path) -> None:
        runtime = tmp_path / "runtime"
        history = tmp_path / "history"
        operator = runtime / "operator"
        operator.mkdir(parents=True)
        history.mkdir(parents=True)
        (runtime / "history").mkdir(parents=True)

        date = "2026-05-01"

        # Seed pick_history with a graded row
        graded = pd.DataFrame([
            _pick_row(
                "Alice",
                prediction_date=date,
                result_status="hit",
                actual_value="22.0",
                fragility_bucket="LOW",
                fragility_score="20.0",
                survivability_bucket="HIGH",
                survivability_score="80.0",
            )
        ])
        graded.to_csv(history / "pick_history.csv", index=False)

        # Write elite board (pending, no fragility fields)
        elite = pd.DataFrame([{
            "player_name": "Alice",
            "player_id": "",
            "team": "BOS",
            "team_abbr": "BOS",
            "opponent": "NYK",
            "game_id": "",
            "market_type": "player_points",
            "selection": "over",
            "sportsbook_line": 20.5,
            "model_projection": 22.0,
            "edge": 1.5,
            "odds": "-110",
            "confidence": 0.75,
            "quality_score": 80.0,
            "qualification_reason": "pass",
            "prediction_date": date,
        }])
        elite.to_csv(operator / f"elite_board_{date}.csv", index=False)

        persist_daily_picks(date, runtime_root=runtime, history_root=history)

        refreshed = pd.read_csv(history / "pick_history.csv")
        alice = refreshed[refreshed["player_name"] == "Alice"].iloc[0]

        # Graded result must not be overwritten
        assert str(alice["result_status"]) == "hit"
        assert str(alice["actual_value"]) == "22.0"
        # Fragility must be preserved
        assert str(alice["fragility_bucket"]) == "LOW"
        assert str(alice["survivability_bucket"]) == "HIGH"
