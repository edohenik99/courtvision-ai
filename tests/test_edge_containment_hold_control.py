"""
Tests for Phase 12G: Edge Containment HOLD Risk Control.

Proves:
- High-edge combo OVERs receive HOLD_FOR_REVIEW metadata
- High-edge non-combo OVERs are NOT held
- Combo UNDERs are NOT held
- Low-edge combo OVERs are NOT held
- Board row counts unchanged (rows visible, not deleted)
- Projections / edge values / confidence unchanged
- Kelly runner forces stake_amount=0 for held rows
- Kelly runner leaves normal rows unchanged
- Hold diagnostics artifact written
- quality_summary includes hold counts
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.edge_containment_hold_control import (
    EDGE_THRESHOLD,
    EVIDENCE_SNAPSHOT,
    HOLD_POLICY_MODE,
    HOLD_POLICY_NAME,
    HOLD_VERDICT,
    _COMBO_MARKETS,
    _build_hold_mask,
    build_hold_control_flags,
    hold_control_json_path_for_date,
    hold_control_review_flags_path_for_date,
    write_hold_control_artifacts,
)

DATE = "2026-05-10"
_COMBO = "player_points_assists"
_NON_COMBO = "player_points"


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _row(
    edge: float = 2.0,
    selection: str = "over",
    market_type: str = _NON_COMBO,
    player_name: str = "Player A",
    line: float = 20.5,
    confidence: float = 0.75,
    context_pick_alignment: str = "aligned",
    is_elite: bool = False,
    model_projection: float = 22.5,
    prediction_date: str = DATE,
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "edge": edge,
        "confidence": confidence,
        "context_pick_alignment": context_pick_alignment,
        "is_elite": is_elite,
        "model_projection": model_projection,
    }


def _make_df(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


# ---------------------------------------------------------------------------
# Tests: _build_hold_mask — policy mask
# ---------------------------------------------------------------------------

class TestBuildHoldMask:
    def test_flags_high_edge_combo_over(self):
        df = _make_df(_row(edge=5.0, selection="over", market_type=_COMBO))
        assert list(_build_hold_mask(df)) == [True]

    def test_does_not_flag_non_combo_over(self):
        df = _make_df(_row(edge=5.0, selection="over", market_type=_NON_COMBO))
        assert list(_build_hold_mask(df)) == [False]

    def test_does_not_flag_combo_under(self):
        df = _make_df(_row(edge=5.0, selection="under", market_type=_COMBO))
        assert list(_build_hold_mask(df)) == [False]

    def test_does_not_flag_low_edge_combo_over(self):
        df = _make_df(_row(edge=3.9, selection="over", market_type=_COMBO))
        assert list(_build_hold_mask(df)) == [False]

    def test_flags_at_exact_threshold(self):
        df = _make_df(_row(edge=4.0, selection="over", market_type=_COMBO))
        assert list(_build_hold_mask(df)) == [True]

    def test_all_four_combo_markets_flagged(self):
        for mkt in _COMBO_MARKETS:
            df = _make_df(_row(edge=5.0, selection="over", market_type=mkt))
            assert list(_build_hold_mask(df)) == [True], f"{mkt} should be held"

    def test_mixed_rows(self):
        rows = [
            _row(edge=5.0, selection="over",  market_type=_COMBO),      # held
            _row(edge=5.0, selection="under", market_type=_COMBO),      # under
            _row(edge=5.0, selection="over",  market_type=_NON_COMBO),  # non-combo
            _row(edge=1.0, selection="over",  market_type=_COMBO),      # low edge
            _row(edge=4.0, selection="over",  market_type=_COMBO),      # held (exact)
        ]
        df = _make_df(*rows)
        assert list(_build_hold_mask(df)) == [True, False, False, False, True]

    def test_missing_edge_column_all_false(self):
        df = pd.DataFrame([{"selection": "over", "market_type": _COMBO}])
        assert list(_build_hold_mask(df)) == [False]

    def test_missing_selection_column_all_false(self):
        df = pd.DataFrame([{"edge": 5.0, "market_type": _COMBO}])
        assert list(_build_hold_mask(df)) == [False]

    def test_missing_market_type_column_all_false(self):
        df = pd.DataFrame([{"edge": 5.0, "selection": "over"}])
        assert list(_build_hold_mask(df)) == [False]

    def test_nan_edge_all_false(self):
        df = _make_df(_row(edge=float("nan"), selection="over", market_type=_COMBO))
        assert list(_build_hold_mask(df)) == [False]


# ---------------------------------------------------------------------------
# Tests: build_hold_control_flags — metadata and counts
# ---------------------------------------------------------------------------

class TestBuildHoldControlFlags:
    def test_empty_df_returns_zero_counts(self):
        result = build_hold_control_flags(pd.DataFrame(), DATE)
        assert result["hold_required_count"] == 0
        assert result["total_rows_checked"] == 0

    def test_hold_required_count_correct(self):
        rows = [
            _row(edge=5.0, selection="over",  market_type=_COMBO),   # held
            _row(edge=5.0, selection="over",  market_type=_COMBO),   # held
            _row(edge=5.0, selection="under", market_type=_COMBO),   # not held
            _row(edge=1.0, selection="over",  market_type=_COMBO),   # not held
        ]
        df = _make_df(*rows)
        result = build_hold_control_flags(df, DATE)
        assert result["hold_required_count"] == 2
        assert result["total_rows_checked"] == 4

    def test_held_rows_get_hold_metadata(self):
        df = _make_df(
            _row(edge=5.0, selection="over", market_type=_COMBO, player_name="A"),
            _row(edge=1.0, selection="over", market_type=_NON_COMBO, player_name="B"),
        )
        result = build_hold_control_flags(df, DATE)
        flags = result["flags_df"]
        held = flags[flags["edge_containment_verdict"] == HOLD_VERDICT]
        assert len(held) == 1
        assert held.iloc[0]["edge_containment_stake_policy"] == "HOLD_FOR_REVIEW"
        assert held.iloc[0]["edge_containment_recommended_action"] == "DO_NOT_BET_UNTIL_REVIEWED"
        assert held.iloc[0]["risk_control_active"] == True
        assert held.iloc[0]["would_block_kelly_stake"] == True
        assert held.iloc[0]["edge_containment_note"] == "risk_control_no_model_change"

    def test_non_held_rows_get_normal_metadata(self):
        df = _make_df(
            _row(edge=5.0, selection="over", market_type=_COMBO),   # held
            _row(edge=1.0, selection="over", market_type=_NON_COMBO), # not held
        )
        result = build_hold_control_flags(df, DATE)
        flags = result["flags_df"]
        not_held = flags[flags["edge_containment_verdict"] != HOLD_VERDICT]
        assert len(not_held) == 1
        assert not_held.iloc[0]["edge_containment_recommended_action"] == "OK_TO_CONSIDER"
        assert not_held.iloc[0]["edge_containment_stake_policy"] == "NORMAL"
        assert not_held.iloc[0]["risk_control_active"] == False
        assert not_held.iloc[0]["would_block_kelly_stake"] == False

    def test_does_not_mutate_input(self):
        df = _make_df(_row(edge=5.0, selection="over", market_type=_COMBO))
        original_cols = set(df.columns)
        build_hold_control_flags(df, DATE)
        assert set(df.columns) == original_cols

    def test_row_count_unchanged(self):
        df = _make_df(
            _row(edge=5.0, selection="over", market_type=_COMBO),
            _row(edge=1.0, selection="over", market_type=_NON_COMBO),
            _row(edge=5.0, selection="under", market_type=_COMBO),
        )
        result = build_hold_control_flags(df, DATE)
        assert len(result["flags_df"]) == 3

    def test_projections_unchanged(self):
        """model_projection values must not be altered."""
        df = _make_df(_row(edge=5.0, selection="over", market_type=_COMBO, model_projection=25.5))
        result = build_hold_control_flags(df, DATE)
        flags = result["flags_df"]
        if "model_projection" in flags.columns:
            assert float(flags.iloc[0]["model_projection"]) == 25.5

    def test_edge_values_unchanged(self):
        """edge column must not be altered."""
        df = _make_df(_row(edge=6.7, selection="over", market_type=_COMBO))
        original_edge = float(df.iloc[0]["edge"])
        result = build_hold_control_flags(df, DATE)
        flags = result["flags_df"]
        if "edge" in flags.columns:
            assert abs(float(flags.iloc[0]["edge"]) - original_edge) < 1e-9

    def test_confidence_unchanged(self):
        df = _make_df(_row(edge=5.0, selection="over", market_type=_COMBO, confidence=0.8))
        original_conf = float(df.iloc[0]["confidence"])
        result = build_hold_control_flags(df, DATE)
        flags = result["flags_df"]
        if "confidence" in flags.columns:
            assert abs(float(flags.iloc[0]["confidence"]) - original_conf) < 1e-9

    def test_elite_hold_count(self):
        df = _make_df(
            _row(edge=5.0, selection="over", market_type=_COMBO, is_elite=True),
            _row(edge=5.0, selection="over", market_type=_COMBO, is_elite=False),
        )
        result = build_hold_control_flags(df, DATE)
        assert result["elite_hold_count"] == 1

    def test_blocked_players_list(self):
        df = _make_df(
            _row(edge=5.0, selection="over", market_type=_COMBO, player_name="Alpha"),
            _row(edge=5.0, selection="over", market_type=_COMBO, player_name="Beta"),
            _row(edge=1.0, selection="over", market_type=_NON_COMBO, player_name="Gamma"),
        )
        result = build_hold_control_flags(df, DATE)
        assert "Alpha" in result["blocked_players"]
        assert "Beta" in result["blocked_players"]
        assert "Gamma" not in result["blocked_players"]

    def test_evidence_snapshot_present(self):
        result = build_hold_control_flags(_make_df(_row()), DATE)
        snap = result["evidence_snapshot"]
        assert snap["graded_flags"] == 13
        assert snap["n_hit"] == 1
        assert snap["n_miss"] == 12

    def test_policy_name_and_mode(self):
        result = build_hold_control_flags(_make_df(_row()), DATE)
        assert result["policy_name"] == HOLD_POLICY_NAME
        assert result["policy_mode"] == HOLD_POLICY_MODE

    def test_kelly_hold_count_passed_through(self):
        result = build_hold_control_flags(_make_df(_row()), DATE, kelly_hold_count=3)
        assert result["kelly_hold_count"] == 3


# ---------------------------------------------------------------------------
# Tests: write_hold_control_artifacts
# ---------------------------------------------------------------------------

class TestWriteHoldControlArtifacts:
    def _prepare(self, tmp_path: Path):
        runtime_root = tmp_path / "outputs" / "runtime"
        (runtime_root / "operator").mkdir(parents=True)
        (runtime_root / "diagnostics").mkdir(parents=True)
        board = _make_df(
            _row(edge=5.0, selection="over", market_type=_COMBO, player_name="Alpha"),
            _row(edge=2.0, selection="over", market_type=_NON_COMBO, player_name="Beta"),
        )
        return runtime_root, board

    def test_returns_three_items(self, tmp_path: Path):
        runtime_root, board = self._prepare(tmp_path)
        result = write_hold_control_artifacts(board, DATE, runtime_root=runtime_root)
        assert len(result) == 3

    def test_review_csv_written(self, tmp_path: Path):
        runtime_root, board = self._prepare(tmp_path)
        review_csv, _, _ = write_hold_control_artifacts(board, DATE, runtime_root=runtime_root)
        assert review_csv.exists()

    def test_hold_json_written(self, tmp_path: Path):
        runtime_root, board = self._prepare(tmp_path)
        _, hold_json, _ = write_hold_control_artifacts(board, DATE, runtime_root=runtime_root)
        assert hold_json.exists()

    def test_hold_json_has_required_keys(self, tmp_path: Path):
        runtime_root, board = self._prepare(tmp_path)
        _, hold_json, _ = write_hold_control_artifacts(board, DATE, runtime_root=runtime_root)
        data = json.loads(hold_json.read_text(encoding="utf-8"))
        for key in (
            "prediction_date", "policy_name", "policy_mode", "policy_reason",
            "total_rows_checked", "hold_required_count", "elite_hold_count",
            "full_market_hold_count", "kelly_hold_count", "kelly_stake_blocked_count",
            "blocked_players", "blocked_markets", "evidence_snapshot", "note",
        ):
            assert key in data, f"missing key: {key}"

    def test_hold_json_evidence_snapshot(self, tmp_path: Path):
        runtime_root, board = self._prepare(tmp_path)
        _, hold_json, _ = write_hold_control_artifacts(board, DATE, runtime_root=runtime_root)
        data = json.loads(hold_json.read_text(encoding="utf-8"))
        snap = data["evidence_snapshot"]
        assert snap["graded_flags"] == 13
        assert snap["hit_rate"] == pytest.approx(0.0769)

    def test_review_csv_row_count_equals_board(self, tmp_path: Path):
        runtime_root, board = self._prepare(tmp_path)
        review_csv, _, _ = write_hold_control_artifacts(board, DATE, runtime_root=runtime_root)
        result_df = pd.read_csv(review_csv)
        assert len(result_df) == len(board)

    def test_review_csv_held_row_has_hold_verdict(self, tmp_path: Path):
        runtime_root, board = self._prepare(tmp_path)
        review_csv, _, _ = write_hold_control_artifacts(board, DATE, runtime_root=runtime_root)
        df = pd.read_csv(review_csv)
        held = df[df["edge_containment_verdict"] == HOLD_VERDICT]
        assert len(held) == 1
        assert held.iloc[0]["player_name"] == "Alpha"

    def test_review_csv_in_operator_subdir(self, tmp_path: Path):
        runtime_root, board = self._prepare(tmp_path)
        review_csv, _, _ = write_hold_control_artifacts(board, DATE, runtime_root=runtime_root)
        assert "operator" in str(review_csv)

    def test_hold_json_in_diagnostics_subdir(self, tmp_path: Path):
        runtime_root, board = self._prepare(tmp_path)
        _, hold_json, _ = write_hold_control_artifacts(board, DATE, runtime_root=runtime_root)
        assert "diagnostics" in str(hold_json)

    def test_payload_no_flags_df_key_in_json(self, tmp_path: Path):
        """flags_df must NOT be serialised into the JSON artifact."""
        runtime_root, board = self._prepare(tmp_path)
        _, hold_json, _ = write_hold_control_artifacts(board, DATE, runtime_root=runtime_root)
        data = json.loads(hold_json.read_text(encoding="utf-8"))
        assert "flags_df" not in data

    def test_empty_board_writes_header_csv(self, tmp_path: Path):
        runtime_root = tmp_path / "outputs" / "runtime"
        (runtime_root / "operator").mkdir(parents=True)
        (runtime_root / "diagnostics").mkdir(parents=True)
        review_csv, _, _ = write_hold_control_artifacts(
            pd.DataFrame(), DATE, runtime_root=runtime_root
        )
        assert review_csv.exists()
        text = review_csv.read_text(encoding="utf-8")
        assert "edge_containment_verdict" in text

    def test_does_not_mutate_input_board(self, tmp_path: Path):
        runtime_root, board = self._prepare(tmp_path)
        original_cols = set(board.columns)
        write_hold_control_artifacts(board, DATE, runtime_root=runtime_root)
        assert set(board.columns) == original_cols


# ---------------------------------------------------------------------------
# Tests: path helpers
# ---------------------------------------------------------------------------

class TestPathHelpers:
    def test_json_path_contains_date(self):
        p = hold_control_json_path_for_date(DATE)
        assert DATE in str(p)

    def test_json_path_in_diagnostics(self):
        p = hold_control_json_path_for_date(DATE)
        assert "diagnostics" in str(p)

    def test_review_flags_path_contains_date(self):
        p = hold_control_review_flags_path_for_date(DATE)
        assert DATE in str(p)

    def test_review_flags_path_in_operator(self):
        p = hold_control_review_flags_path_for_date(DATE)
        assert "operator" in str(p)

    def test_review_flags_same_name_as_phase_12e(self):
        """Phase 12G CSV path must match Phase 12E so it overwrites Phase 12E output."""
        from courtvision.reporting.edge_containment_review import review_flags_path_for_date
        p12e = review_flags_path_for_date(DATE)
        p12g = hold_control_review_flags_path_for_date(DATE)
        assert p12e.name == p12g.name


# ---------------------------------------------------------------------------
# Tests: Kelly hold integration
# ---------------------------------------------------------------------------

class TestKellyHoldIntegration:
    """Verify Phase 12G hold behavior in the Kelly runner."""

    def _combo_row(
        self,
        edge_pct: str = "0.405",  # decimal fraction  (> 0.04 threshold)
        selection: str = "over",
        market_type: str = _COMBO,
        player_name: str = "ComboPlayer",
    ) -> dict[str, str]:
        return {
            "player_name": player_name,
            "market_type": market_type,
            "selection": selection,
            "line": "20.5",
            "odds": "-110",
            "confidence": "0.80",
            "edge_pct": edge_pct,
            "side_edge_pct": edge_pct,
        }

    def _normal_row(
        self,
        edge_pct: str = "0.10",
        selection: str = "over",
        player_name: str = "NormalPlayer",
    ) -> dict[str, str]:
        return {
            "player_name": player_name,
            "market_type": "player_points",
            "selection": selection,
            "line": "20.5",
            "odds": "-110",
            "confidence": "0.80",
            "edge_pct": edge_pct,
            "side_edge_pct": edge_pct,
        }

    def test_held_combo_over_gets_zero_stake(self):
        from scripts.run_kelly_stakes import _build_stake_row
        row = self._combo_row()
        stake = _build_stake_row(row, "side_edge_pct", 1000.0)
        assert stake.stake_amount == 0.0
        assert stake.stake_fraction == 0.0

    def test_held_combo_over_gets_hold_skip_reason(self):
        from scripts.run_kelly_stakes import (
            EDGE_CONTAINMENT_HOLD_SKIP_REASON,
            _build_stake_row,
        )
        stake = _build_stake_row(self._combo_row(), "side_edge_pct", 1000.0)
        assert stake.skip_reason == EDGE_CONTAINMENT_HOLD_SKIP_REASON

    def test_held_combo_over_gets_hold_stake_policy(self):
        from scripts.run_kelly_stakes import _build_stake_row
        stake = _build_stake_row(self._combo_row(), "side_edge_pct", 1000.0)
        assert stake.stake_policy == "HOLD_FOR_REVIEW"

    def test_held_combo_over_gets_do_not_bet_recommended_action(self):
        from scripts.run_kelly_stakes import _build_stake_row
        stake = _build_stake_row(self._combo_row(), "side_edge_pct", 1000.0)
        assert stake.recommended_action == "DO_NOT_BET_UNTIL_REVIEWED"

    def test_held_combo_over_gets_hold_review_status(self):
        from scripts.run_kelly_stakes import _build_stake_row
        stake = _build_stake_row(self._combo_row(), "side_edge_pct", 1000.0)
        assert stake.review_status == "HOLD"

    def test_held_combo_over_sets_manual_review_required(self):
        from scripts.run_kelly_stakes import _build_stake_row
        stake = _build_stake_row(self._combo_row(), "side_edge_pct", 1000.0)
        assert stake.manual_review_required is True

    def test_held_combo_over_not_eligible(self):
        from scripts.run_kelly_stakes import _build_stake_row
        stake = _build_stake_row(self._combo_row(), "side_edge_pct", 1000.0)
        assert stake.eligible is False

    def test_metadata_flag_triggers_hold_for_player_points(self):
        """Metadata-based hold fires even for player_points if explicitly flagged."""
        from scripts.run_kelly_stakes import _build_stake_row
        row = {
            **self._normal_row(edge_pct="0.10"),
            "edge_containment_stake_policy": "HOLD_FOR_REVIEW",
        }
        stake = _build_stake_row(row, "side_edge_pct", 1000.0)
        assert stake.stake_amount == 0.0
        assert stake.review_status == "HOLD"

    def test_metadata_recommended_action_triggers_hold(self):
        from scripts.run_kelly_stakes import _build_stake_row
        row = {
            **self._normal_row(edge_pct="0.10"),
            "edge_containment_recommended_action": "DO_NOT_BET_UNTIL_REVIEWED",
        }
        stake = _build_stake_row(row, "side_edge_pct", 1000.0)
        assert stake.stake_amount == 0.0
        assert stake.review_status == "HOLD"

    def test_normal_player_points_over_not_held(self):
        from scripts.run_kelly_stakes import _build_stake_row
        stake = _build_stake_row(self._normal_row(selection="over"), "side_edge_pct", 1000.0)
        assert stake.eligible is True
        assert stake.stake_amount > 0
        assert stake.review_status == "CLEAR"

    def test_normal_player_points_under_not_held(self):
        from scripts.run_kelly_stakes import _build_stake_row
        stake = _build_stake_row(self._normal_row(selection="under"), "side_edge_pct", 1000.0)
        assert stake.eligible is True
        assert stake.stake_amount > 0

    def test_combo_under_not_held(self):
        """Combo UNDERs must NOT be held — only combo OVERs are in scope."""
        from scripts.run_kelly_stakes import _build_stake_row
        row = self._combo_row(selection="under")
        stake = _build_stake_row(row, "side_edge_pct", 1000.0)
        # Not held — may be ineligible for other reasons (market_type lock)
        assert stake.review_status != "HOLD"
        assert stake.stake_policy != "HOLD_FOR_REVIEW"

    def test_low_edge_combo_over_not_held(self):
        """Combo OVER with edge < 0.04 must NOT be held."""
        from scripts.run_kelly_stakes import _build_stake_row
        row = self._combo_row(edge_pct="0.03")   # 3% < 4% threshold
        stake = _build_stake_row(row, "side_edge_pct", 1000.0)
        assert stake.review_status != "HOLD"
        assert stake.stake_policy != "HOLD_FOR_REVIEW"

    def test_normal_kelly_math_unchanged(self):
        """Normal player_points rows must produce the same stake as before Phase 12G."""
        from courtvision.betting.kelly import compute_kelly_fraction
        from scripts.run_kelly_stakes import _build_stake_row
        row = self._normal_row(edge_pct="0.10")
        stake = _build_stake_row(row, "side_edge_pct", 1000.0)
        expected_frac = compute_kelly_fraction(0.10, 1.9091, 0.80)
        assert stake.stake_fraction == pytest.approx(expected_frac, abs=1e-6)

    def test_total_exposure_excludes_held_rows(self):
        """Held rows must contribute $0 to total exposure."""
        from scripts.run_kelly_stakes import _build_stake_row
        normal = _build_stake_row(self._normal_row(), "side_edge_pct", 1000.0)
        combo  = _build_stake_row(self._combo_row(), "side_edge_pct", 1000.0)
        eligible_stakes = [s for s in [normal, combo] if s.eligible]
        total_exposure = sum(s.stake_amount for s in eligible_stakes)
        # Only normal row is eligible; combo is held (eligible=False)
        assert total_exposure == pytest.approx(normal.stake_amount, abs=0.01)
        assert combo.stake_amount == 0.0


# ---------------------------------------------------------------------------
# Tests: Kelly end-to-end via main() with CSV
# ---------------------------------------------------------------------------

class TestKellyMainHoldIntegration:
    def _write_elite_board(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            if not rows:
                return
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    def test_main_held_rows_zero_stake_in_csv(self, tmp_path: Path):
        from scripts.run_kelly_stakes import main
        elite_path = tmp_path / "elite_board_2026-05-01.csv"
        out_path   = tmp_path / "kelly_stakes_2026-05-01.csv"
        rows = [
            {   # normal player_points OVER — should get stake
                "player_name": "Normal", "market_type": "player_points",
                "selection": "over", "line": "20.5", "odds": "-110",
                "confidence": "0.80", "side_edge_pct": "0.10", "edge_pct": "0.10",
            },
            {   # combo OVER — should be held, stake = 0
                "player_name": "Held", "market_type": "player_points_assists",
                "selection": "over", "line": "20.5", "odds": "-110",
                "confidence": "0.80", "side_edge_pct": "0.405", "edge_pct": "0.405",
            },
        ]
        self._write_elite_board(elite_path, rows)
        ret = main([
            "--prediction-date", "2026-05-01",
            "--bankroll", "1000",
            "--input-csv", str(elite_path),
            "--output-csv", str(out_path),
        ])
        assert ret == 0
        stakes = list(csv.DictReader(out_path.open("r", encoding="utf-8")))
        normal_row = next(s for s in stakes if s["player_name"] == "Normal")
        held_row   = next(s for s in stakes if s["player_name"] == "Held")
        assert float(normal_row["stake_amount"]) > 0, "Normal row should have positive stake"
        assert float(held_row["stake_amount"]) == 0.0, "Held row must have zero stake"
        assert held_row["stake_policy"] == "HOLD_FOR_REVIEW"
        assert held_row["review_status"] == "HOLD"

    def test_main_normal_stakes_unchanged_after_phase12g(self, tmp_path: Path):
        """Normal Kelly picks must produce the same amount whether hold-check fires or not."""
        from scripts.run_kelly_stakes import main
        elite_path = tmp_path / "elite_board_2026-05-02.csv"
        out_path   = tmp_path / "kelly_stakes_2026-05-02.csv"
        rows = [{
            "player_name": "Normal", "market_type": "player_points",
            "selection": "over", "line": "20.5", "odds": "-110",
            "confidence": "0.80", "side_edge_pct": "0.10", "edge_pct": "0.10",
        }]
        self._write_elite_board(elite_path, rows)
        main([
            "--prediction-date", "2026-05-02",
            "--bankroll", "1000",
            "--input-csv", str(elite_path),
            "--output-csv", str(out_path),
        ])
        stakes = list(csv.DictReader(out_path.open("r", encoding="utf-8")))
        assert float(stakes[0]["stake_amount"]) == pytest.approx(20.0, abs=1.0)


# ---------------------------------------------------------------------------
# Tests: quality_summary integration
# ---------------------------------------------------------------------------

class TestQualitySummaryIntegration:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path, str]:
        runtime_root = tmp_path / "outputs" / "runtime"
        operator = runtime_root / "operator"
        diagnostics = runtime_root / "diagnostics"
        research = runtime_root / "research"
        history = tmp_path / "data" / "history"
        for d in (operator, diagnostics, research, history):
            d.mkdir(parents=True, exist_ok=True)
        date = DATE
        board_rows = [
            {
                "prediction_date": date, "player_name": "Alpha",
                "market_type": _COMBO, "selection": "over",
                "line": 20.5, "edge": 5.5, "confidence": 0.75,
                "context_pick_alignment": "conflicted", "is_elite": False,
            },
            {
                "prediction_date": date, "player_name": "Beta",
                "market_type": "player_points", "selection": "over",
                "line": 22.5, "edge": 2.1, "confidence": 0.70,
                "context_pick_alignment": "aligned", "is_elite": False,
            },
        ]
        pd.DataFrame([{"prediction_date": date}]).to_csv(
            operator / f"elite_board_{date}.csv", index=False
        )
        pd.DataFrame(board_rows).to_csv(
            operator / f"full_market_board_{date}.csv", index=False
        )
        # Write header-only kelly stakes (empty DataFrame([]) produces an empty file
        # that pd.read_csv rejects; header-only is always safe)
        (operator / f"kelly_stakes_{date}.csv").write_text(
            "player_name,stake_amount,skip_reason,stake_policy,review_status\n",
            encoding="utf-8",
        )
        (research / f"player_predictions_{date}.csv").write_text("", encoding="utf-8")
        (research / f"model_metrics_{date}.json").write_text("{}", encoding="utf-8")
        (operator / f"elite_pipeline_audit_summary_{date}.json").write_text("{}", encoding="utf-8")
        (diagnostics / f"board_diagnostics_{date}.json").write_text("{}", encoding="utf-8")
        return runtime_root, history, date

    def test_key_present_in_payload(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        assert "edge_containment_hold_control" in qs

    def test_payload_has_required_keys(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        ehc = qs["edge_containment_hold_control"]
        for key in (
            "edge_containment_policy_active",
            "edge_containment_policy_mode",
            "edge_containment_hold_count",
            "edge_containment_kelly_blocked_count",
            "edge_containment_hold_artifact",
            "edge_containment_note",
        ):
            assert key in ehc, f"missing key: {key}"

    def test_policy_mode_is_hold_for_review(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        assert qs["edge_containment_hold_control"]["edge_containment_policy_mode"] == "HOLD_FOR_REVIEW"

    def test_hold_count_correct(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        # Board has 1 combo OVER with edge=5.5 (> 4.0 threshold) → hold_count = 1
        assert qs["edge_containment_hold_control"]["edge_containment_hold_count"] == 1

    def test_hold_artifact_written(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        hold_path = Path(qs["edge_containment_hold_control"]["edge_containment_hold_artifact"])
        assert hold_path.exists()

    def test_quality_summary_txt_contains_hold_section(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        text_path, _, _ = write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        text = text_path.read_text(encoding="utf-8")
        assert "EDGE CONTAINMENT HOLD CONTROL" in text
        assert "HOLD_FOR_REVIEW" in text

    def test_full_market_board_row_count_unchanged(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        board_path = runtime_root / "operator" / f"full_market_board_{date}.csv"
        rows_before = len(pd.read_csv(board_path))
        write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        assert len(pd.read_csv(board_path)) == rows_before

    def test_elite_board_row_count_unchanged(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        elite_path = runtime_root / "operator" / f"elite_board_{date}.csv"
        rows_before = len(pd.read_csv(elite_path))
        write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        assert len(pd.read_csv(elite_path)) == rows_before

    def test_no_crash_when_board_absent(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        (runtime_root / "operator" / f"full_market_board_{date}.csv").unlink()
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        assert "edge_containment_hold_control" in qs
