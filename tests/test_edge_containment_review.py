"""
Tests for Phase 12E: Edge Containment Live Review Overlay.

Proves:
- High-edge combo OVERs are flagged for review
- High-edge non-combo OVERs are NOT flagged
- Combo UNDERs are NOT flagged
- Low-edge combo OVERs are NOT flagged
- Elite/full-market row counts do not change
- Kelly math is not touched
- Review artifact is written
- quality_summary includes review counts
- No live suppression occurs
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.edge_containment_review import (
    EDGE_THRESHOLD,
    REVIEW_POLICY_NAME,
    _COMBO_MARKETS,
    _build_review_mask,
    build_edge_containment_review_flags,
    review_flags_path_for_date,
    write_edge_containment_review_flags,
)

DATE = "2026-05-10"
_COMBO = list(_COMBO_MARKETS)[0]   # e.g. "player_points_assists"
_NON_COMBO = "player_points"


# ---------------------------------------------------------------------------
# Row/DataFrame factories
# ---------------------------------------------------------------------------

def _row(
    edge: float = 2.0,
    selection: str = "over",
    market_type: str = _NON_COMBO,
    player_name: str = "Player A",
    game_id: str = "G1",
    confidence: float = 0.75,
    line: float = 20.5,
    context_pick_alignment: str = "aligned",
    is_elite: bool = False,
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
        "game_id": game_id,
    }


def _make_df(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


# ---------------------------------------------------------------------------
# Tests: _build_review_mask
# ---------------------------------------------------------------------------

class TestBuildReviewMask:
    def test_flags_high_edge_combo_over(self):
        df = _make_df(_row(edge=5.0, selection="over", market_type=_COMBO))
        assert list(_build_review_mask(df)) == [True]

    def test_does_not_flag_non_combo_over(self):
        df = _make_df(_row(edge=5.0, selection="over", market_type=_NON_COMBO))
        assert list(_build_review_mask(df)) == [False]

    def test_does_not_flag_combo_under(self):
        df = _make_df(_row(edge=5.0, selection="under", market_type=_COMBO))
        assert list(_build_review_mask(df)) == [False]

    def test_does_not_flag_low_edge_combo_over(self):
        df = _make_df(_row(edge=3.9, selection="over", market_type=_COMBO))
        assert list(_build_review_mask(df)) == [False]

    def test_flags_at_exact_threshold(self):
        df = _make_df(_row(edge=4.0, selection="over", market_type=_COMBO))
        assert list(_build_review_mask(df)) == [True]

    def test_all_combo_markets_flagged(self):
        for mkt in _COMBO_MARKETS:
            df = _make_df(_row(edge=5.0, selection="over", market_type=mkt))
            mask = _build_review_mask(df)
            assert list(mask) == [True], f"{mkt} should be flagged"

    def test_mixed_rows(self):
        rows = [
            _row(edge=5.0, selection="over",  market_type=_COMBO),       # flagged
            _row(edge=5.0, selection="under", market_type=_COMBO),       # under
            _row(edge=5.0, selection="over",  market_type=_NON_COMBO),   # non-combo
            _row(edge=1.0, selection="over",  market_type=_COMBO),       # low edge
            _row(edge=6.0, selection="over",  market_type=_COMBO),       # flagged
        ]
        df = _make_df(*rows)
        assert list(_build_review_mask(df)) == [True, False, False, False, True]

    def test_missing_edge_column_all_false(self):
        df = pd.DataFrame([{"selection": "over", "market_type": _COMBO}])
        assert list(_build_review_mask(df)) == [False]

    def test_missing_selection_column_all_false(self):
        df = pd.DataFrame([{"edge": 5.0, "market_type": _COMBO}])
        assert list(_build_review_mask(df)) == [False]

    def test_nan_edge_all_false(self):
        df = _make_df(_row(edge=float("nan"), selection="over", market_type=_COMBO))
        assert list(_build_review_mask(df)) == [False]


# ---------------------------------------------------------------------------
# Tests: build_edge_containment_review_flags
# ---------------------------------------------------------------------------

class TestBuildEdgeContainmentReviewFlags:
    def test_empty_df_returns_zero_counts(self):
        result = build_edge_containment_review_flags(pd.DataFrame(), DATE)
        assert result["total_rows_checked"] == 0
        assert result["review_required_count"] == 0
        assert result["high_edge_combo_over_review_count"] == 0

    def test_review_required_count_correct(self):
        rows = [
            _row(edge=5.0, selection="over",  market_type=_COMBO),   # flagged
            _row(edge=5.0, selection="over",  market_type=_COMBO),   # flagged
            _row(edge=5.0, selection="under", market_type=_COMBO),   # not flagged
            _row(edge=1.0, selection="over",  market_type=_COMBO),   # not flagged
        ]
        df = _make_df(*rows)
        result = build_edge_containment_review_flags(df, DATE)
        assert result["review_required_count"] == 2
        assert result["total_rows_checked"] == 4

    def test_flags_df_has_correct_metadata_for_flagged_rows(self):
        df = _make_df(
            _row(edge=5.0, selection="over", market_type=_COMBO, player_name="A"),
            _row(edge=1.0, selection="over", market_type=_NON_COMBO, player_name="B"),
        )
        result = build_edge_containment_review_flags(df, DATE)
        flags_df = result["flags_df"]
        flagged = flags_df[flags_df["edge_containment_review_required"] == True]
        assert len(flagged) == 1
        assert flagged.iloc[0]["edge_containment_policy"] == REVIEW_POLICY_NAME
        assert flagged.iloc[0]["edge_containment_reason"] == "high_edge_combo_over_historical_inflation"
        assert flagged.iloc[0]["edge_containment_recommended_action"] == "REVIEW_BEFORE_BET"
        assert flagged.iloc[0]["edge_containment_note"] == "review_only_no_live_suppression"

    def test_non_flagged_rows_get_ok_metadata(self):
        df = _make_df(
            _row(edge=5.0, selection="over", market_type=_COMBO),    # flagged
            _row(edge=1.0, selection="over", market_type=_NON_COMBO), # not flagged
        )
        result = build_edge_containment_review_flags(df, DATE)
        flags_df = result["flags_df"]
        not_flagged = flags_df[flags_df["edge_containment_review_required"] == False]
        assert len(not_flagged) == 1
        assert not_flagged.iloc[0]["edge_containment_recommended_action"] == "OK_TO_CONSIDER"
        assert not_flagged.iloc[0]["edge_containment_policy"] == ""

    def test_does_not_mutate_input_df(self):
        df = _make_df(
            _row(edge=5.0, selection="over", market_type=_COMBO),
            _row(edge=1.0, selection="over", market_type=_NON_COMBO),
        )
        original_len  = len(df)
        original_cols = set(df.columns)
        build_edge_containment_review_flags(df, DATE)
        assert len(df) == original_len
        assert set(df.columns) == original_cols
        assert "edge_containment_review_required" not in df.columns

    def test_row_count_unchanged(self):
        # Flags df has same number of rows as input df
        rows = [_row(edge=5.0, selection="over", market_type=_COMBO)] * 3
        rows += [_row(edge=1.0)] * 5
        df = _make_df(*rows)
        result = build_edge_containment_review_flags(df, DATE)
        assert len(result["flags_df"]) == len(df)

    def test_elite_flagged_count_using_is_elite(self):
        rows = [
            _row(edge=5.0, selection="over", market_type=_COMBO, is_elite=True),
            _row(edge=5.0, selection="over", market_type=_COMBO, is_elite=False),
            _row(edge=1.0, selection="over", market_type=_NON_COMBO, is_elite=True),
        ]
        df = _make_df(*rows)
        result = build_edge_containment_review_flags(df, DATE)
        assert result["review_required_count"] == 2
        assert result["elite_review_required_count"] == 1

    def test_flagged_players_list(self):
        rows = [
            _row(edge=5.0, selection="over", market_type=_COMBO, player_name="Alpha"),
            _row(edge=5.0, selection="over", market_type=_COMBO, player_name="Beta"),
            _row(edge=1.0, selection="over", market_type=_NON_COMBO, player_name="Gamma"),
        ]
        df = _make_df(*rows)
        result = build_edge_containment_review_flags(df, DATE)
        assert "Alpha" in result["flagged_players"]
        assert "Beta" in result["flagged_players"]
        assert "Gamma" not in result["flagged_players"]

    def test_policy_name_in_payload(self):
        df = _make_df(_row(edge=5.0, selection="over", market_type=_COMBO))
        result = build_edge_containment_review_flags(df, DATE)
        assert result["policy_name"] == REVIEW_POLICY_NAME

    def test_review_note_present(self):
        df = _make_df(_row(edge=5.0, selection="over", market_type=_COMBO))
        result = build_edge_containment_review_flags(df, DATE)
        assert "REVIEW_ONLY" in result["review_note"] or "review" in result["review_note"].lower()

    def test_no_live_suppression_all_rows_present(self):
        # Flagging must not remove rows
        rows = [
            _row(edge=5.0, selection="over", market_type=_COMBO),
            _row(edge=5.0, selection="over", market_type=_COMBO),
            _row(edge=1.0, selection="over", market_type=_NON_COMBO),
        ]
        df = _make_df(*rows)
        result = build_edge_containment_review_flags(df, DATE)
        assert len(result["flags_df"]) == 3

    def test_high_edge_non_combo_over_not_flagged(self):
        df = _make_df(_row(edge=7.0, selection="over", market_type=_NON_COMBO))
        result = build_edge_containment_review_flags(df, DATE)
        assert result["review_required_count"] == 0

    def test_combo_under_not_flagged(self):
        df = _make_df(_row(edge=7.0, selection="under", market_type=_COMBO))
        result = build_edge_containment_review_flags(df, DATE)
        assert result["review_required_count"] == 0

    def test_flagged_markets_list_contains_combo_types(self):
        rows = [_row(edge=5.0, selection="over", market_type=mkt) for mkt in list(_COMBO_MARKETS)[:2]]
        df = _make_df(*rows)
        result = build_edge_containment_review_flags(df, DATE)
        for mkt in list(_COMBO_MARKETS)[:2]:
            assert mkt in result["flagged_markets"]


# ---------------------------------------------------------------------------
# Tests: write_edge_containment_review_flags
# ---------------------------------------------------------------------------

class TestWriteEdgeContainmentReviewFlags:
    def test_writes_csv_artifact(self, tmp_path: Path):
        rows = [_row(edge=5.0, selection="over", market_type=_COMBO)]
        df = _make_df(*rows)
        csv_path, payload = write_edge_containment_review_flags(df, DATE, tmp_path)
        assert csv_path.exists()

    def test_csv_has_required_columns(self, tmp_path: Path):
        df = _make_df(
            _row(edge=5.0, selection="over", market_type=_COMBO),
            _row(edge=1.0, selection="over", market_type=_NON_COMBO),
        )
        csv_path, _ = write_edge_containment_review_flags(df, DATE, tmp_path)
        result_df = pd.read_csv(csv_path)
        for col in (
            "player_name", "market_type", "selection", "edge",
            "edge_containment_review_required",
            "edge_containment_policy",
            "edge_containment_reason",
            "edge_containment_recommended_action",
        ):
            assert col in result_df.columns, f"missing column: {col}"

    def test_csv_row_count_equals_input(self, tmp_path: Path):
        rows = [_row()] * 7
        df = _make_df(*rows)
        csv_path, _ = write_edge_containment_review_flags(df, DATE, tmp_path)
        result_df = pd.read_csv(csv_path)
        assert len(result_df) == 7

    def test_empty_df_writes_header_only_csv(self, tmp_path: Path):
        csv_path, payload = write_edge_containment_review_flags(pd.DataFrame(), DATE, tmp_path)
        assert csv_path.exists()
        content = csv_path.read_text(encoding="utf-8")
        assert "edge_containment_review_required" in content

    def test_payload_has_no_flags_df_key(self, tmp_path: Path):
        df = _make_df(_row(edge=5.0, selection="over", market_type=_COMBO))
        _, payload = write_edge_containment_review_flags(df, DATE, tmp_path)
        # The serialisable payload should NOT include the DataFrame
        assert "flags_df" not in payload

    def test_csv_in_operator_subdir(self, tmp_path: Path):
        df = _make_df(_row())
        csv_path, _ = write_edge_containment_review_flags(df, DATE, tmp_path)
        assert "operator" in str(csv_path)

    def test_does_not_modify_input_df(self, tmp_path: Path):
        df = _make_df(
            _row(edge=5.0, selection="over", market_type=_COMBO),
            _row(edge=1.0),
        )
        original_cols = set(df.columns)
        original_len  = len(df)
        write_edge_containment_review_flags(df, DATE, tmp_path)
        assert set(df.columns) == original_cols
        assert len(df) == original_len

    def test_flagged_rows_have_review_true_in_csv(self, tmp_path: Path):
        df = _make_df(
            _row(edge=5.0, selection="over", market_type=_COMBO, player_name="FlaggedPlayer"),
            _row(edge=1.0, selection="over", market_type=_NON_COMBO, player_name="ClearPlayer"),
        )
        csv_path, _ = write_edge_containment_review_flags(df, DATE, tmp_path)
        result_df = pd.read_csv(csv_path)
        flagged = result_df[result_df["player_name"] == "FlaggedPlayer"]
        clear   = result_df[result_df["player_name"] == "ClearPlayer"]
        assert bool(flagged.iloc[0]["edge_containment_review_required"]) is True
        assert bool(clear.iloc[0]["edge_containment_review_required"])   is False

    def test_kelly_stakes_file_unchanged(self, tmp_path: Path):
        """Writing review flags must never modify Kelly stakes CSV."""
        kelly_path = tmp_path / "operator" / f"kelly_stakes_{DATE}.csv"
        kelly_path.parent.mkdir(parents=True, exist_ok=True)
        kelly_content = "player_name,stake_amount\nMikal Bridges,20.00\n"
        kelly_path.write_text(kelly_content, encoding="utf-8")
        df = _make_df(_row(edge=5.0, selection="over", market_type=_COMBO))
        write_edge_containment_review_flags(df, DATE, tmp_path)
        assert kelly_path.read_text(encoding="utf-8") == kelly_content

    def test_elite_board_file_unchanged(self, tmp_path: Path):
        """Writing review flags must never modify elite board CSV."""
        elite_path = tmp_path / "operator" / f"elite_board_{DATE}.csv"
        elite_path.parent.mkdir(parents=True, exist_ok=True)
        elite_content = "player_name,edge\nMikal Bridges,0.21\n"
        elite_path.write_text(elite_content, encoding="utf-8")
        df = _make_df(_row(edge=5.0, selection="over", market_type=_COMBO))
        write_edge_containment_review_flags(df, DATE, tmp_path)
        assert elite_path.read_text(encoding="utf-8") == elite_content

    def test_full_market_board_file_unchanged(self, tmp_path: Path):
        """Writing review flags must never modify full_market_board CSV."""
        board_path = tmp_path / "operator" / f"full_market_board_{DATE}.csv"
        board_path.parent.mkdir(parents=True, exist_ok=True)
        board_content = "player_name,edge\nMikal Bridges,5.5\n"
        board_path.write_text(board_content, encoding="utf-8")
        df = _make_df(_row(edge=5.0, selection="over", market_type=_COMBO))
        write_edge_containment_review_flags(df, DATE, tmp_path)
        assert board_path.read_text(encoding="utf-8") == board_content


# ---------------------------------------------------------------------------
# Tests: path helper
# ---------------------------------------------------------------------------

class TestPathHelper:
    def test_path_contains_date_and_filename(self):
        p = review_flags_path_for_date("2026-05-10", "outputs/runtime")
        assert "2026-05-10" in str(p)
        assert "edge_containment_review_flags" in str(p)
        assert str(p).endswith(".csv")

    def test_path_in_operator_subdir(self):
        p = review_flags_path_for_date("2026-05-10", "outputs/runtime")
        assert "operator" in str(p)


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
        # Build a small full_market_board with some flagged combo OVERs
        board_rows = [
            {
                "prediction_date": date, "player_name": "Alpha",
                "market_type": _COMBO, "selection": "over",
                "line": 20.5, "edge": 5.5, "confidence": 0.75,
                "context_pick_alignment": "conflicted", "is_elite": True,
            },
            {
                "prediction_date": date, "player_name": "Beta",
                "market_type": _NON_COMBO, "selection": "over",
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
        pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{date}.csv", index=False)
        (research / f"player_predictions_{date}.csv").write_text("", encoding="utf-8")
        (research / f"model_metrics_{date}.json").write_text("{}", encoding="utf-8")
        (operator / f"elite_pipeline_audit_summary_{date}.json").write_text("{}", encoding="utf-8")
        (diagnostics / f"board_diagnostics_{date}.json").write_text("{}", encoding="utf-8")
        return runtime_root, history, date

    def test_key_present_in_payload(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        assert "edge_containment_review" in qs

    def test_payload_has_required_keys(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        ecr = qs["edge_containment_review"]
        for key in (
            "csv_path", "policy_name", "total_rows_checked",
            "review_required_count", "high_edge_combo_over_review_count",
            "elite_review_required_count", "full_market_review_required_count",
            "note",
        ):
            assert key in ecr, f"missing key: {key}"

    def test_note_is_review_only(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        note = qs["edge_containment_review"]["note"]
        assert "no_live_suppression" in note or "review_only" in note

    def test_review_csv_artifact_exists(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        csv_path = Path(qs["edge_containment_review"]["csv_path"])
        assert csv_path.exists()

    def test_review_count_correct(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        ecr = qs["edge_containment_review"]
        # Board has 1 row with edge=5.5, over, combo → review_required_count = 1
        assert ecr["review_required_count"] == 1
        assert ecr["total_rows_checked"] == 2

    def test_elite_board_count_unchanged(self, tmp_path: Path):
        """elite_board CSV must not have rows added or removed."""
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        elite_path = runtime_root / "operator" / f"elite_board_{date}.csv"
        elite_rows_before = len(pd.read_csv(elite_path))
        write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        assert len(pd.read_csv(elite_path)) == elite_rows_before

    def test_full_market_board_row_count_unchanged(self, tmp_path: Path):
        """full_market_board must not gain or lose rows after quality_summary runs.

        Note: annotate_operator_board_files (existing pipeline code) may add
        annotation columns to the board as part of normal operation. This test
        verifies row count only — no rows should be suppressed or removed by
        the Phase 12E overlay.
        """
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        board_path = runtime_root / "operator" / f"full_market_board_{date}.csv"
        rows_before = len(pd.read_csv(board_path))
        write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        assert len(pd.read_csv(board_path)) == rows_before

    def test_quality_summary_txt_contains_review_section(self, tmp_path: Path):
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        text_path, _, _ = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        text = text_path.read_text(encoding="utf-8")
        assert "EDGE CONTAINMENT REVIEW OVERLAY" in text
        assert "NO ROWS SUPPRESSED" in text

    def test_no_board_no_crash(self, tmp_path: Path):
        """If full_market_board is absent, quality_summary must not crash."""
        from courtvision.reporting.quality_summary import write_quality_summary_outputs
        runtime_root, history, date = self._setup(tmp_path)
        board_path = runtime_root / "operator" / f"full_market_board_{date}.csv"
        board_path.unlink()  # remove it
        _, _, qs = write_quality_summary_outputs(
            prediction_date=date,
            runtime_root=runtime_root,
            out_dir=tmp_path / "outputs",
            history_root=history,
        )
        assert "edge_containment_review" in qs
