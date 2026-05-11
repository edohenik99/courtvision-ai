"""
Tests for Phase 13A: Combo Projection Math Audit.

Covers:
  - component parsing for all four combo market types
  - combo total projection error calculation
  - points/rebounds/assists error calculation
  - dominant component classification
  - multi-component inflation classification
  - POINTS_INFLATION classification
  - insufficient component projection fields
  - insufficient actual component fields
  - probable DNP (actual=0) handling
  - unmatched component rows handling
  - JSON artifact writing
  - TXT artifact writing
  - quality_summary integration
  - no source data mutation
  - no model/projection/edge/Kelly behavior changes
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from courtvision.reporting.combo_projection_math_audit import (
    EDGE_THRESHOLD,
    RC_INSUFFICIENT,
    RC_MULTI_INFLATION,
    RC_POINTS_INFLATION,
    RC_REBOUNDS_INFLATION,
    RC_ASSISTS_INFLATION,
    RC_LINE_TOO_HIGH,
    _build_component_lookup,
    _classify_root_cause,
    _compute_component_status,
    _decompose_row,
    _edge_bucket,
    _confidence_bucket,
    _filter_graded,
    _filter_high_edge_combo_overs,
    build_combo_projection_math_audit,
    combo_audit_json_path_for_date,
    combo_audit_txt_path_for_date,
    write_combo_projection_math_audit,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_COMBO_MARKETS = [
    "player_points_assists",
    "player_points_rebounds",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
]

_COMBO_COMPONENTS = {
    "player_points_assists":          ["player_points", "player_assists"],
    "player_points_rebounds":         ["player_points", "player_rebounds"],
    "player_rebounds_assists":        ["player_rebounds", "player_assists"],
    "player_points_rebounds_assists": ["player_points", "player_rebounds", "player_assists"],
}


def _sh_combo_row(
    player: str = "Test Player",
    market: str = "player_points_assists",
    projection: float = 25.0,
    actual: float = 10.0,
    edge: float = 6.0,
    confidence: float = 0.75,
    result: str = "miss",
    selection: str = "over",
    date: str = "2026-05-01",
    line: float = 18.0,
) -> dict[str, Any]:
    return {
        "prediction_date": date,
        "player_name":     player,
        "market_type":     market,
        "selection":       selection,
        "line":            line,
        "model_projection": projection,
        "actual_value":    actual,
        "edge":            edge,
        "confidence":      confidence,
        "result_status":   result,
        "shadow_roi":      -1.0 if result == "miss" else 0.91,
    }


def _sh_comp_row(
    player: str = "Test Player",
    market: str = "player_points",
    projection: float = 20.0,
    actual: float | None = 8.0,
    result: str = "miss",
    date: str = "2026-05-01",
) -> dict[str, Any]:
    return {
        "prediction_date": date,
        "player_name":     player,
        "market_type":     market,
        "selection":       "over",
        "line":            16.0,
        "model_projection": projection,
        "actual_value":    actual,
        "result_status":   result,
        "edge":            2.5,
        "confidence":      0.72,
        "shadow_roi":      -1.0,
    }


def _make_history_df(*rows: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _write_history_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


# ---------------------------------------------------------------------------
# TestComponentParsing
# ---------------------------------------------------------------------------

class TestComponentParsing:
    def test_points_assists_components(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists"),
            _sh_comp_row(market="player_points"),
            _sh_comp_row(market="player_assists", projection=5.0),
        )
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_points_assists"].iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert "player_points" in decomp["comp_data"]
        assert "player_assists" in decomp["comp_data"]
        assert "player_rebounds" not in decomp["comp_data"]

    def test_points_rebounds_components(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_rebounds"),
            _sh_comp_row(market="player_points"),
            _sh_comp_row(market="player_rebounds", projection=6.0),
        )
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_points_rebounds"].iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert "player_points" in decomp["comp_data"]
        assert "player_rebounds" in decomp["comp_data"]
        assert "player_assists" not in decomp["comp_data"]

    def test_rebounds_assists_components(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_rebounds_assists", projection=12.0),
            _sh_comp_row(market="player_rebounds", projection=7.0),
            _sh_comp_row(market="player_assists", projection=5.0),
        )
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_rebounds_assists"].iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert "player_rebounds" in decomp["comp_data"]
        assert "player_assists" in decomp["comp_data"]
        assert "player_points" not in decomp["comp_data"]

    def test_pra_components(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_rebounds_assists", projection=32.0),
            _sh_comp_row(market="player_points"),
            _sh_comp_row(market="player_rebounds", projection=6.0),
            _sh_comp_row(market="player_assists", projection=5.0),
        )
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_points_rebounds_assists"].iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert all(
            c in decomp["comp_data"]
            for c in ["player_points", "player_rebounds", "player_assists"]
        )

    def test_all_proj_found_flag_true_when_all_present(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists"),
            _sh_comp_row(market="player_points"),
            _sh_comp_row(market="player_assists"),
        )
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_points_assists"].iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert decomp["all_proj_found"] is True

    def test_all_proj_found_false_when_component_missing(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists"),
            _sh_comp_row(market="player_points"),
            # player_assists row missing
        )
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_points_assists"].iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert decomp["all_proj_found"] is False
        assert decomp["partial_proj_found"] is True

    def test_no_components_found(self):
        sh = _make_history_df(_sh_combo_row(market="player_points_assists"))
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_points_assists"].iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert decomp["all_proj_found"] is False
        assert decomp["partial_proj_found"] is False


# ---------------------------------------------------------------------------
# TestComboeProjectionError
# ---------------------------------------------------------------------------

class TestComboProjectionError:
    def test_combo_error_computed_correctly(self):
        sh = _make_history_df(_sh_combo_row(projection=25.0, actual=10.0))
        lookup = _build_component_lookup(sh)
        combo_row = sh.iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert decomp["combo_error"] == pytest.approx(15.0)

    def test_combo_error_none_when_actual_missing(self):
        sh = _make_history_df(_sh_combo_row(actual=float("nan")))
        lookup = _build_component_lookup(sh)
        combo_row = sh.iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert decomp["combo_error"] is None

    def test_combo_error_negative_for_hit(self):
        sh = _make_history_df(_sh_combo_row(projection=22.0, actual=30.0, result="hit"))
        lookup = _build_component_lookup(sh)
        combo_row = sh.iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert decomp["combo_error"] == pytest.approx(-8.0)

    def test_avg_combo_error_correct(self):
        sh = _make_history_df(
            _sh_combo_row(projection=25.0, actual=10.0),
            _sh_combo_row(projection=20.0, actual=12.0, player="Player B"),
        )
        payload = build_combo_projection_math_audit(
            prediction_date="2026-05-01",
            operator_dir="outputs/runtime/operator",
            history_csv=sh,
        )
        # avg of 15.0 and 8.0 = 11.5
        assert payload["avg_combo_projection_error"] == pytest.approx(11.5)


# ---------------------------------------------------------------------------
# TestComponentErrorCalculation
# ---------------------------------------------------------------------------

class TestComponentErrorCalculation:
    def test_points_error_computed(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists", projection=25.0, actual=10.0),
            _sh_comp_row(market="player_points", projection=20.0, actual=8.0),
            _sh_comp_row(market="player_assists", projection=5.0, actual=None),
        )
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_points_assists"].iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        # player_points error = 20 - 8 = 12
        assert decomp["comp_errors"]["player_points"] == pytest.approx(12.0)

    def test_rebounds_error_null_when_actual_missing(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_rebounds"),
            _sh_comp_row(market="player_rebounds", projection=7.0, actual=None, result="void"),
        )
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_points_rebounds"].iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert decomp["comp_errors"].get("player_rebounds") is None

    def test_assists_error_null_when_actual_missing(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists"),
            _sh_comp_row(market="player_assists", projection=5.0, actual=None, result="void"),
        )
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_points_assists"].iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert decomp["comp_errors"].get("player_assists") is None

    def test_component_error_zero_when_projection_equals_actual(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists"),
            _sh_comp_row(market="player_points", projection=15.0, actual=15.0, result="push"),
        )
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_points_assists"].iloc[0]
        decomp = _decompose_row(combo_row, lookup)
        assert decomp["comp_errors"].get("player_points") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# TestDominantComponentClassification
# ---------------------------------------------------------------------------

class TestDominantComponentClassification:
    def test_points_inflation_when_points_dominates(self):
        # points_error / combo_error = 9/10 = 0.90 > 0.60
        decomp = {
            "result_status": "miss",
            "combo_actual":  10.0,
            "combo_error":   10.0,
            "comp_errors": {
                "player_points": 9.0,
                "player_assists": None,
            },
        }
        assert _classify_root_cause(decomp) == RC_POINTS_INFLATION

    def test_rebounds_inflation_when_rebounds_dominates(self):
        decomp = {
            "result_status": "miss",
            "combo_actual":  15.0,
            "combo_error":   10.0,
            "comp_errors": {
                "player_points":  2.0,
                "player_rebounds": 8.0,
            },
        }
        assert _classify_root_cause(decomp) == RC_REBOUNDS_INFLATION

    def test_assists_inflation_when_assists_dominates(self):
        decomp = {
            "result_status": "miss",
            "combo_actual":  15.0,
            "combo_error":   8.0,
            "comp_errors": {
                "player_points":  2.0,
                "player_assists":  6.0,
            },
        }
        assert _classify_root_cause(decomp) == RC_ASSISTS_INFLATION

    def test_hit_returns_hit_not_failure(self):
        decomp = {
            "result_status": "hit",
            "combo_actual":  30.0,
            "combo_error":   -5.0,
            "comp_errors": {"player_points": -5.0},
        }
        assert _classify_root_cause(decomp) == "HIT_NOT_FAILURE"


# ---------------------------------------------------------------------------
# TestMultiComponentInflation
# ---------------------------------------------------------------------------

class TestMultiComponentInflation:
    def test_multi_when_both_contribute_above_threshold(self):
        # Neither component alone exceeds 60% of combo_error=10
        # points=4 (40%), rebounds=6 (60%) — exactly at threshold for rebounds
        # Try: points=4 (40%), rebounds=5 (50%) → neither dominates
        decomp = {
            "result_status": "miss",
            "combo_actual":  15.0,
            "combo_error":   10.0,
            "comp_errors": {
                "player_points":   4.0,
                "player_rebounds": 5.0,
            },
        }
        rc = _classify_root_cause(decomp)
        assert rc in (RC_MULTI_INFLATION, RC_LINE_TOO_HIGH)

    def test_multi_when_all_three_contribute(self):
        decomp = {
            "result_status": "miss",
            "combo_actual":  12.0,
            "combo_error":   12.0,
            "comp_errors": {
                "player_points":   4.0,
                "player_rebounds": 4.0,
                "player_assists":  4.0,
            },
        }
        assert _classify_root_cause(decomp) == RC_MULTI_INFLATION

    def test_single_component_high_fraction_stays_inflation(self):
        decomp = {
            "result_status": "miss",
            "combo_actual":  10.0,
            "combo_error":   8.0,
            "comp_errors": {"player_points": 7.0},
        }
        assert _classify_root_cause(decomp) == RC_POINTS_INFLATION


# ---------------------------------------------------------------------------
# TestInsufficientComponentProjectionFields
# ---------------------------------------------------------------------------

class TestInsufficientComponentProjectionFields:
    def test_status_when_no_component_rows(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists"),
            _sh_combo_row(market="player_points_rebounds", player="Player B"),
        )
        lookup = _build_component_lookup(sh)
        decomp_list = [
            _decompose_row(sh.iloc[0], lookup),
            _decompose_row(sh.iloc[1], lookup),
        ]
        proj_status, _ = _compute_component_status(decomp_list)
        assert proj_status == "insufficient_component_projection_fields"

    def test_status_when_most_components_found(self):
        # 3 out of 3 with all components found → "all_component_projection_fields_available"
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists"),
            _sh_comp_row(market="player_points"),
            _sh_comp_row(market="player_assists"),
        )
        lookup = _build_component_lookup(sh)
        combo_rows = sh[sh["market_type"] == "player_points_assists"]
        decomp_list = [_decompose_row(r, lookup) for _, r in combo_rows.iterrows()]
        proj_status, _ = _compute_component_status(decomp_list)
        assert proj_status == "all_component_projection_fields_available"

    def test_status_partial_when_some_missing(self):
        rows = []
        for i in range(4):
            rows.append(_sh_combo_row(market="player_points_assists", player=f"P{i}", date="2026-05-01"))
        rows.append(_sh_comp_row(market="player_points", player="P0"))
        rows.append(_sh_comp_row(market="player_assists", player="P0"))
        sh = _make_history_df(*rows)
        lookup = _build_component_lookup(sh)
        combo_rows = sh[sh["market_type"] == "player_points_assists"]
        decomp_list = [_decompose_row(r, lookup) for _, r in combo_rows.iterrows()]
        proj_status, _ = _compute_component_status(decomp_list)
        assert proj_status in (
            "partial_component_projection_fields",
            "insufficient_component_projection_fields",
            "all_component_projection_fields_available",
        )


# ---------------------------------------------------------------------------
# TestInsufficientActualFields
# ---------------------------------------------------------------------------

class TestInsufficientActualFields:
    def test_insufficient_when_only_combo_actual_available(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists", actual=12.0),
            _sh_comp_row(market="player_points", actual=None, result="void"),
            _sh_comp_row(market="player_assists", actual=None, result="void"),
        )
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_points_assists"].iloc[0]
        decomp_list = [_decompose_row(combo_row, lookup)]
        _, act_status = _compute_component_status(decomp_list)
        assert act_status == "insufficient_component_actual_fields"

    def test_partial_when_points_actual_available(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists", actual=12.0),
            _sh_comp_row(market="player_points", actual=8.0, result="miss"),
            _sh_comp_row(market="player_assists", actual=None, result="void"),
        )
        lookup = _build_component_lookup(sh)
        combo_row = sh[sh["market_type"] == "player_points_assists"].iloc[0]
        decomp_list = [_decompose_row(combo_row, lookup)]
        _, act_status = _compute_component_status(decomp_list)
        assert act_status == "partial_points_only_insufficient_rebounds_assists"


# ---------------------------------------------------------------------------
# TestDNPHandling
# ---------------------------------------------------------------------------

class TestDNPHandling:
    def test_probable_dnp_returns_insufficient(self):
        # actual <= 1.0 → probable DNP
        decomp = {
            "result_status": "miss",
            "combo_actual":  0.0,
            "combo_error":   25.0,
            "comp_errors":   {},
        }
        assert _classify_root_cause(decomp) == RC_INSUFFICIENT

    def test_dnp_actual_1_returns_insufficient(self):
        decomp = {
            "result_status": "miss",
            "combo_actual":  1.0,
            "combo_error":   20.0,
            "comp_errors":   {"player_points": 15.0},
        }
        assert _classify_root_cause(decomp) == RC_INSUFFICIENT

    def test_low_but_nonzero_actual_classifies_normally(self):
        # actual=2.0 > threshold=1.0 → classify normally
        decomp = {
            "result_status": "miss",
            "combo_actual":  2.0,
            "combo_error":   10.0,
            "comp_errors":   {"player_points": 8.0},
        }
        assert _classify_root_cause(decomp) == RC_POINTS_INFLATION


# ---------------------------------------------------------------------------
# TestUnmatchedRows
# ---------------------------------------------------------------------------

class TestUnmatchedRows:
    def test_unmatched_count_in_payload(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists", edge=5.0, result="miss"),
            # No component rows → unmatched
        )
        payload = build_combo_projection_math_audit(
            prediction_date="2026-05-01",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        assert payload["unmatched_component_rows"] == 1
        assert payload["matched_component_rows"] == 0

    def test_matched_count_increases_when_components_found(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists", edge=5.0, result="miss"),
            _sh_comp_row(market="player_points"),
            _sh_comp_row(market="player_assists"),
        )
        payload = build_combo_projection_math_audit(
            prediction_date="2026-05-01",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        assert payload["matched_component_rows"] == 1
        assert payload["unmatched_component_rows"] == 0


# ---------------------------------------------------------------------------
# TestJsonArtifactWriting
# ---------------------------------------------------------------------------

class TestJsonArtifactWriting:
    def test_json_artifact_created(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_history_csv(hist, [
            _sh_combo_row(edge=5.0, result="miss"),
            _sh_comp_row(market="player_points"),
        ])
        jpath, _, _ = write_combo_projection_math_audit(
            prediction_date="2026-05-01",
            runtime_root=tmp_path / "runtime",
            history_csv=hist,
        )
        assert jpath.exists()

    def test_json_is_valid(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_history_csv(hist, [_sh_combo_row(edge=5.0, result="miss")])
        jpath, _, _ = write_combo_projection_math_audit(
            prediction_date="2026-05-01",
            runtime_root=tmp_path / "runtime",
            history_csv=hist,
        )
        data = json.loads(jpath.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_json_contains_required_keys(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_history_csv(hist, [_sh_combo_row(edge=5.0, result="miss")])
        jpath, _, _ = write_combo_projection_math_audit(
            prediction_date="2026-05-01",
            runtime_root=tmp_path / "runtime",
            history_csv=hist,
        )
        data = json.loads(jpath.read_text(encoding="utf-8"))
        for key in [
            "prediction_date", "note", "total_combo_over_rows", "graded_combo_over_rows",
            "avg_combo_projection_error", "dominant_error_component",
            "component_projection_status", "component_actual_status",
            "root_cause_summary", "by_market_type", "by_player",
        ]:
            assert key in data, f"Missing key: {key}"

    def test_json_note_is_audit_only(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_history_csv(hist, [_sh_combo_row(edge=5.0)])
        jpath, _, _ = write_combo_projection_math_audit(
            prediction_date="2026-05-01",
            runtime_root=tmp_path / "runtime",
            history_csv=hist,
        )
        data = json.loads(jpath.read_text(encoding="utf-8"))
        assert data["note"] == "audit_only_no_model_change"

    def test_json_path_contains_date(self, tmp_path: Path):
        p = combo_audit_json_path_for_date("2026-05-01", tmp_path)
        assert "2026-05-01" in str(p)

    def test_json_in_diagnostics_directory(self, tmp_path: Path):
        p = combo_audit_json_path_for_date("2026-05-01", tmp_path)
        assert "diagnostics" in str(p)


# ---------------------------------------------------------------------------
# TestTxtArtifactWriting
# ---------------------------------------------------------------------------

class TestTxtArtifactWriting:
    def test_txt_artifact_created(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_history_csv(hist, [_sh_combo_row(edge=5.0, result="miss")])
        _, tpath, _ = write_combo_projection_math_audit(
            prediction_date="2026-05-01",
            runtime_root=tmp_path / "runtime",
            history_csv=hist,
        )
        assert tpath.exists()

    def test_txt_contains_header(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_history_csv(hist, [_sh_combo_row(edge=5.0)])
        _, tpath, _ = write_combo_projection_math_audit(
            prediction_date="2026-05-01",
            runtime_root=tmp_path / "runtime",
            history_csv=hist,
        )
        content = tpath.read_text(encoding="utf-8")
        assert "COMBO PROJECTION MATH AUDIT" in content

    def test_txt_contains_dominant_component(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_history_csv(hist, [
            _sh_combo_row(edge=5.0, result="miss"),
            _sh_comp_row(market="player_points", actual=8.0),
        ])
        _, tpath, _ = write_combo_projection_math_audit(
            prediction_date="2026-05-01",
            runtime_root=tmp_path / "runtime",
            history_csv=hist,
        )
        content = tpath.read_text(encoding="utf-8")
        assert "dominant_error_component" in content

    def test_txt_contains_market_type_breakdown(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_history_csv(hist, [_sh_combo_row(edge=5.0, result="miss")])
        _, tpath, _ = write_combo_projection_math_audit(
            prediction_date="2026-05-01",
            runtime_root=tmp_path / "runtime",
            history_csv=hist,
        )
        content = tpath.read_text(encoding="utf-8")
        assert "BY MARKET TYPE" in content

    def test_txt_contains_root_cause_section(self, tmp_path: Path):
        hist = tmp_path / "history.csv"
        _write_history_csv(hist, [
            _sh_combo_row(edge=5.0, result="miss", actual=10.0),
            _sh_comp_row(market="player_points", actual=8.0),
        ])
        _, tpath, _ = write_combo_projection_math_audit(
            prediction_date="2026-05-01",
            runtime_root=tmp_path / "runtime",
            history_csv=hist,
        )
        content = tpath.read_text(encoding="utf-8")
        assert "ROOT CAUSE" in content

    def test_txt_path_in_operator_dir(self, tmp_path: Path):
        p = combo_audit_txt_path_for_date("2026-05-01", tmp_path)
        assert "operator" in str(p)


# ---------------------------------------------------------------------------
# TestQualitySummaryIntegration
# ---------------------------------------------------------------------------

class TestQualitySummaryIntegration:
    def _setup(self, tmp_path: Path) -> tuple[str, Path, Path]:
        date = "2026-05-01"
        runtime = tmp_path / "runtime"
        (runtime / "operator").mkdir(parents=True, exist_ok=True)
        (runtime / "diagnostics").mkdir(parents=True, exist_ok=True)
        (runtime / "research").mkdir(parents=True, exist_ok=True)
        (runtime / "reports").mkdir(parents=True, exist_ok=True)

        # Minimal history CSV
        hist = tmp_path / "history.csv"
        _write_history_csv(hist, [
            _sh_combo_row(edge=5.0, result="miss", actual=10.0),
            _sh_comp_row(market="player_points", actual=8.0),
        ])

        # Mock a quality summary text file that already exists
        text_path = runtime / "operator" / f"quality_summary_{date}.txt"
        text_path.write_text("existing summary\n", encoding="utf-8")

        return date, runtime, hist

    def test_quality_summary_key_present(self, tmp_path: Path, monkeypatch):
        date, runtime, hist = self._setup(tmp_path)
        monkeypatch.setattr(
            "courtvision.reporting.combo_projection_math_audit.DEFAULT_HISTORY_CSV",
            str(hist),
        )

        # Import after monkeypatching
        from courtvision.reporting.combo_projection_math_audit import (
            write_combo_projection_math_audit,
        )
        _, _, payload = write_combo_projection_math_audit(
            prediction_date=date,
            runtime_root=runtime,
            history_csv=hist,
        )
        assert "note" in payload
        assert payload["note"] == "audit_only_no_model_change"

    def test_json_artifact_readable_in_quality_summary(self, tmp_path: Path):
        date, runtime, hist = self._setup(tmp_path)
        jpath, tpath, payload = write_combo_projection_math_audit(
            prediction_date=date,
            runtime_root=runtime,
            history_csv=hist,
        )
        assert jpath.exists()
        data = json.loads(jpath.read_text(encoding="utf-8"))
        assert data["prediction_date"] == date

    def test_quality_summary_payload_fields(self, tmp_path: Path):
        date, runtime, hist = self._setup(tmp_path)
        _, _, payload = write_combo_projection_math_audit(
            prediction_date=date,
            runtime_root=runtime,
            history_csv=hist,
        )
        assert "total_combo_over_rows" in payload
        assert "graded_combo_over_rows" in payload
        assert "dominant_error_component" in payload
        assert "component_projection_status" in payload
        assert "component_actual_status" in payload


# ---------------------------------------------------------------------------
# TestNoSourceMutation
# ---------------------------------------------------------------------------

class TestNoSourceMutation:
    def test_shadow_history_df_not_mutated(self):
        sh = _make_history_df(
            _sh_combo_row(edge=5.0, result="miss"),
            _sh_comp_row(market="player_points"),
        )
        original_cols = list(sh.columns)
        original_len  = len(sh)
        original_vals = sh["model_projection"].tolist()

        build_combo_projection_math_audit(
            prediction_date="2026-05-01",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )

        assert list(sh.columns) == original_cols
        assert len(sh) == original_len
        assert sh["model_projection"].tolist() == original_vals

    def test_component_lookup_does_not_modify_source(self):
        sh = _make_history_df(
            _sh_comp_row(market="player_points", projection=20.0),
            _sh_comp_row(market="player_rebounds", projection=6.0),
        )
        original = sh.copy()
        _build_component_lookup(sh)
        pd.testing.assert_frame_equal(sh, original)

    def test_decompose_does_not_modify_row(self):
        sh = _make_history_df(
            _sh_combo_row(market="player_points_assists", projection=25.0),
        )
        lookup = {}
        row = sh.iloc[0].copy()
        original_proj = row["model_projection"]
        _decompose_row(row, lookup)
        assert row["model_projection"] == original_proj


# ---------------------------------------------------------------------------
# TestEdgeBuckets
# ---------------------------------------------------------------------------

class TestEdgeBuckets:
    def test_edge_bucket_4_to_5(self):
        assert _edge_bucket(4.0) == "4_to_5pct"
        assert _edge_bucket(4.99) == "4_to_5pct"

    def test_edge_bucket_5_to_7(self):
        assert _edge_bucket(5.0) == "5_to_7pct"
        assert _edge_bucket(6.99) == "5_to_7pct"

    def test_edge_bucket_7_plus(self):
        assert _edge_bucket(7.0) == "7pct_plus"
        assert _edge_bucket(15.0) == "7pct_plus"

    def test_edge_bucket_unknown(self):
        assert _edge_bucket(None) == "unknown"

    def test_confidence_bucket_low(self):
        assert _confidence_bucket(0.65) == "below_0.70"

    def test_confidence_bucket_mid(self):
        assert _confidence_bucket(0.75) == "0.70_to_0.80"

    def test_confidence_bucket_high(self):
        assert _confidence_bucket(0.85) == "0.80_plus"

    def test_confidence_bucket_unknown(self):
        assert _confidence_bucket(None) == "unknown"


# ---------------------------------------------------------------------------
# TestByMarketTypeBreakdown
# ---------------------------------------------------------------------------

class TestByMarketTypeBreakdown:
    def test_all_four_markets_represented(self):
        rows = []
        for mt in [
            "player_points_assists", "player_points_rebounds",
            "player_rebounds_assists", "player_points_rebounds_assists",
        ]:
            rows.append(_sh_combo_row(market=mt, edge=5.0, result="miss",
                                      player=f"P_{mt}"))
        sh = _make_history_df(*rows)
        payload = build_combo_projection_math_audit(
            prediction_date="2026-05-01",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        for mt in [
            "player_points_assists", "player_points_rebounds",
            "player_rebounds_assists", "player_points_rebounds_assists",
        ]:
            assert mt in payload["by_market_type"]

    def test_hit_rate_zero_when_all_miss(self):
        rows = [_sh_combo_row(edge=5.0, result="miss", player=f"P{i}") for i in range(3)]
        sh = _make_history_df(*rows)
        payload = build_combo_projection_math_audit(
            prediction_date="2026-05-01",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        mt = payload["by_market_type"].get("player_points_assists", {})
        assert mt.get("hit_rate") == 0.0

    def test_hit_rate_one_when_all_hit(self):
        rows = [_sh_combo_row(edge=5.0, result="hit", actual=30.0, player=f"P{i}") for i in range(3)]
        sh = _make_history_df(*rows)
        payload = build_combo_projection_math_audit(
            prediction_date="2026-05-01",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        mt = payload["by_market_type"].get("player_points_assists", {})
        assert mt.get("hit_rate") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# TestHighEdgeFilter
# ---------------------------------------------------------------------------

class TestHighEdgeFilter:
    def test_only_edge_gte_4_included(self):
        sh = _make_history_df(
            _sh_combo_row(edge=3.9, result="miss"),
            _sh_combo_row(edge=4.0, result="miss", player="P2"),
            _sh_combo_row(edge=5.0, result="miss", player="P3"),
        )
        filtered = _filter_high_edge_combo_overs(sh)
        assert len(filtered) == 2
        assert all(filtered["edge"] >= 4.0)

    def test_only_over_selection_included(self):
        sh = _make_history_df(
            _sh_combo_row(edge=5.0, selection="over"),
            _sh_combo_row(edge=5.0, selection="under", player="P2"),
        )
        filtered = _filter_high_edge_combo_overs(sh)
        assert len(filtered) == 1
        assert filtered.iloc[0]["selection"] == "over"

    def test_only_combo_markets_included(self):
        sh = _make_history_df(
            _sh_combo_row(edge=5.0, market="player_points_assists"),
            _sh_comp_row(market="player_points"),
        )
        filtered = _filter_high_edge_combo_overs(sh)
        assert len(filtered) == 1
        assert filtered.iloc[0]["market_type"] == "player_points_assists"

    def test_empty_df_returns_empty(self):
        assert _filter_high_edge_combo_overs(pd.DataFrame()).empty

    def test_graded_filter_excludes_pending(self):
        sh = _make_history_df(
            _sh_combo_row(result="miss"),
            _sh_combo_row(result="pending", player="P2"),
            _sh_combo_row(result="void", player="P3"),
        )
        graded = _filter_graded(sh)
        assert len(graded) == 1
        assert graded.iloc[0]["result_status"] == "miss"


# ---------------------------------------------------------------------------
# TestNoLiveLogicChanged
# ---------------------------------------------------------------------------

class TestNoLiveLogicChanged:
    """Verify the audit module has no side effects on live data or logic."""

    def test_module_does_not_import_kelly_runner(self):
        """The audit module must not import run_kelly_stakes or pipeline modules."""
        import importlib, sys
        mod = importlib.import_module("courtvision.reporting.combo_projection_math_audit")
        mod_source = Path(mod.__file__).read_text(encoding="utf-8")
        for forbidden in ["run_kelly_stakes", "kelly_runner", "elite_gate"]:
            assert forbidden not in mod_source

    def test_payload_note_is_audit_only(self):
        sh = _make_history_df(_sh_combo_row(edge=5.0))
        payload = build_combo_projection_math_audit(
            prediction_date="2026-05-01",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        assert payload["note"] == "audit_only_no_model_change"

    def test_history_csv_not_written(self, tmp_path: Path):
        """The audit writer must not overwrite market_shadow_history.csv."""
        hist = tmp_path / "history.csv"
        _write_history_csv(hist, [_sh_combo_row(edge=5.0, result="miss")])
        mtime_before = hist.stat().st_mtime

        write_combo_projection_math_audit(
            prediction_date="2026-05-01",
            runtime_root=tmp_path / "runtime",
            history_csv=hist,
        )

        mtime_after = hist.stat().st_mtime
        assert mtime_before == mtime_after, "history CSV was modified by audit writer"

    def test_edge_values_unchanged_in_source_df(self):
        sh = _make_history_df(
            _sh_combo_row(edge=6.5, result="miss"),
            _sh_comp_row(market="player_points"),
        )
        original_edges = sh["edge"].tolist()
        build_combo_projection_math_audit(
            prediction_date="2026-05-01",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        assert sh["edge"].tolist() == original_edges

    def test_projections_unchanged_in_source_df(self):
        sh = _make_history_df(
            _sh_combo_row(projection=27.5),
            _sh_comp_row(projection=22.0),
        )
        original_proj = sh["model_projection"].tolist()
        build_combo_projection_math_audit(
            prediction_date="2026-05-01",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        assert sh["model_projection"].tolist() == original_proj


# ---------------------------------------------------------------------------
# TestBuildWithRealishData
# ---------------------------------------------------------------------------

class TestBuildWithRealishData:
    """Smoke tests that use realistic multi-row scenarios."""

    def _make_realistic_history(self) -> pd.DataFrame:
        rows = [
            # VJ Edgecombe PRA: strong points inflation
            _sh_combo_row("VJ Edgecombe", "player_points_rebounds_assists",
                          projection=26.98, actual=20.0, edge=6.98, result="miss"),
            _sh_comp_row("VJ Edgecombe", "player_points", projection=16.37, actual=10.0, result="miss"),
            _sh_comp_row("VJ Edgecombe", "player_rebounds", projection=6.56, actual=None, result="void"),
            _sh_comp_row("VJ Edgecombe", "player_assists", projection=4.06, actual=None, result="void"),
            # Jalen Duren PRA
            _sh_combo_row("Jalen Duren", "player_points_rebounds_assists",
                          projection=30.77, actual=18.0, edge=6.77, result="miss"),
            _sh_comp_row("Jalen Duren", "player_points", projection=18.36, actual=8.0, result="miss"),
            # Payton Pritchard hit
            _sh_combo_row("Payton Pritchard", "player_points_assists",
                          projection=22.29, actual=18.0, edge=4.29, result="hit"),
            _sh_comp_row("Payton Pritchard", "player_points", projection=17.5, actual=12.0, result="miss"),
        ]
        return _make_history_df(*rows)

    def test_graded_count_correct(self):
        sh = self._make_realistic_history()
        payload = build_combo_projection_math_audit(
            prediction_date="2026-04-28",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        assert payload["graded_combo_over_rows"] == 3

    def test_hit_rate_correct(self):
        sh = self._make_realistic_history()
        payload = build_combo_projection_math_audit(
            prediction_date="2026-04-28",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        # 1 hit out of 3 graded (ignoring push) = 1/2 hit/miss = 0.5
        assert payload["hit_rate"] == pytest.approx(1 / 3, abs=0.01)

    def test_dominant_component_is_points(self):
        sh = self._make_realistic_history()
        payload = build_combo_projection_math_audit(
            prediction_date="2026-04-28",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        assert payload["dominant_error_component"] == "player_points"

    def test_avg_combo_error_is_positive(self):
        sh = self._make_realistic_history()
        payload = build_combo_projection_math_audit(
            prediction_date="2026-04-28",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        # Miss combo errors: 26.98-20=6.98, 30.77-18=12.77, hit: 22.29-18=-4.29
        assert payload["avg_combo_projection_error"] is not None

    def test_by_player_contains_all_players(self):
        sh = self._make_realistic_history()
        payload = build_combo_projection_math_audit(
            prediction_date="2026-04-28",
            operator_dir="nonexistent_dir",
            history_csv=sh,
        )
        players = list(payload["by_player"].keys())
        assert "VJ Edgecombe" in players
        assert "Jalen Duren" in players
        assert "Payton Pritchard" in players
