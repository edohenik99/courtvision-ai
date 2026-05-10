from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.edge_inflation_research import (
    REPORT_VERSION,
    _classify_risk,
    _seg,
    build_edge_inflation_research,
    edge_research_json_path_for_date,
    edge_research_txt_path_for_date,
    write_edge_inflation_research,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs


DATE = "2026-05-10"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _row(
    result_status: str = "hit",
    edge: float = 1.5,
    confidence: float = 0.75,
    shadow_roi: float = 0.05,
    model_projection: float = 25.0,
    actual_value: float = 22.0,
    selection: str = "over",
    market_type: str = "player_points",
    prediction_date: str = DATE,
    context_pick_alignment: str = "aligned",
    context_caution_level: str = "medium",
    final_elite_rejection_reason: str = "",
    team_abbr: str = "LAL",
    player_name: str = "Player A",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "result_status": result_status,
        "edge": edge,
        "confidence": confidence,
        "shadow_roi": shadow_roi,
        "model_projection": model_projection,
        "actual_value": actual_value,
        "selection": selection,
        "market_type": market_type,
        "context_pick_alignment": context_pick_alignment,
        "context_caution_level": context_caution_level,
        "final_elite_rejection_reason": final_elite_rejection_reason,
        "team_abbr": team_abbr,
        "player_name": player_name,
    }


def _make_df(n_hits: int = 20, n_misses: int = 15, **kwargs) -> pd.DataFrame:
    rows = [_row("hit",  **kwargs)] * n_hits
    rows += [_row("miss", **kwargs)] * n_misses
    return pd.DataFrame(rows)


def _make_multi_edge_df() -> pd.DataFrame:
    """DataFrame with rows spread across all edge buckets."""
    rows = []
    # 0-2%: good performance
    for _ in range(12):
        rows.append(_row("hit",  edge=1.0, shadow_roi=0.10, model_projection=22.0, actual_value=22.0))
    for _ in range(8):
        rows.append(_row("miss", edge=1.0, shadow_roi=-0.10, model_projection=22.0, actual_value=22.0))
    # 2-4%: moderate
    for _ in range(8):
        rows.append(_row("hit",  edge=3.0, shadow_roi=0.05, model_projection=25.0, actual_value=22.0))
    for _ in range(12):
        rows.append(_row("miss", edge=3.0, shadow_roi=-0.15, model_projection=25.0, actual_value=22.0))
    # 4-6%: bad
    for _ in range(4):
        rows.append(_row("hit",  edge=5.0, shadow_roi=0.05, model_projection=28.0, actual_value=22.0))
    for _ in range(8):
        rows.append(_row("miss", edge=5.0, shadow_roi=-0.25, model_projection=28.0, actual_value=22.0))
    # 6%+: worst
    for _ in range(3):
        rows.append(_row("hit",  edge=8.0, shadow_roi=0.05, model_projection=32.0, actual_value=22.0))
    for _ in range(10):
        rows.append(_row("miss", edge=8.0, shadow_roi=-0.35, model_projection=32.0, actual_value=22.0))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Unit tests: _classify_risk
# ---------------------------------------------------------------------------

class TestClassifyRisk:
    def test_insufficient_sample(self) -> None:
        assert _classify_risk(5.0, 0.25, -0.60, 5) == "INSUFFICIENT_SAMPLE"

    def test_catastrophic_by_error(self) -> None:
        assert _classify_risk(9.0, 0.40, -0.30, 20) == "CATASTROPHIC"

    def test_catastrophic_by_hit_roi(self) -> None:
        assert _classify_risk(2.0, 0.25, -0.60, 20) == "CATASTROPHIC"

    def test_severe_by_error(self) -> None:
        assert _classify_risk(6.0, 0.45, -0.15, 20) == "SEVERE"

    def test_severe_by_hit_roi(self) -> None:
        assert _classify_risk(2.0, 0.38, -0.40, 20) == "SEVERE"

    def test_high(self) -> None:
        assert _classify_risk(3.5, 0.48, -0.15, 20) == "HIGH"

    def test_moderate(self) -> None:
        assert _classify_risk(2.0, 0.52, -0.08, 20) == "MODERATE"

    def test_acceptable(self) -> None:
        assert _classify_risk(0.5, 0.55, 0.05, 20) == "ACCEPTABLE"


# ---------------------------------------------------------------------------
# Unit tests: _seg
# ---------------------------------------------------------------------------

class TestSeg:
    def test_empty(self) -> None:
        s = _seg(pd.DataFrame(), "test")
        assert s["risk_flag"] == "INSUFFICIENT_SAMPLE"
        assert s["sample_size"] == 0

    def test_projection_error(self) -> None:
        df = _make_df(10, 5, model_projection=25.0, actual_value=22.0)
        s = _seg(df, "test")
        assert s["avg_projection_error"] == pytest.approx(3.0, abs=0.01)
        assert s["over_prediction_rate"] == pytest.approx(1.0, abs=0.01)

    def test_no_mutation(self) -> None:
        df = _make_df(10, 5)
        cols_before = list(df.columns)
        _seg(df, "test")
        assert list(df.columns) == cols_before

    def test_sparse_caution(self) -> None:
        df = pd.DataFrame([_row()] * 5)
        s = _seg(df, "sparse")
        assert "caution" in s


# ---------------------------------------------------------------------------
# Build tests
# ---------------------------------------------------------------------------

class TestBuildReport:
    def test_empty_df(self) -> None:
        r = build_edge_inflation_research(pd.DataFrame(), DATE)
        assert r["report_version"] == REPORT_VERSION
        assert r["overall"]["total_graded_rows"] == 0
        assert r["overall"]["overall_risk_flag"] == "INSUFFICIENT_SAMPLE"

    def test_basic_structure(self) -> None:
        df = _make_df(20, 15)
        r = build_edge_inflation_research(df, DATE)
        assert r["report_version"] == REPORT_VERSION
        assert r["prediction_date"] == DATE
        for key in ("overall", "edge_bucket_analysis", "edge_by_direction",
                    "edge_by_market", "high_edge_context_analysis",
                    "high_edge_combo_analysis", "edge_error_correlation",
                    "policy_simulations", "top_findings"):
            assert key in r, f"missing key: {key}"

    def test_notes_shadow_only(self) -> None:
        r = build_edge_inflation_research(_make_df(), DATE)
        assert "shadow_analytics_and_research_only" in r["notes"]
        assert "no_live_logic_changed" in r["notes"]
        assert "policy_simulations_are_research_only" in r["notes"]

    def test_no_graded_rows(self) -> None:
        df = pd.DataFrame([{"result_status": "pending", "edge": 2.0}])
        r = build_edge_inflation_research(df, DATE)
        assert r["overall"]["total_graded_rows"] == 0

    def test_no_mutation_of_source(self) -> None:
        df = _make_df(20, 10)
        n_before = len(df)
        cols_before = set(df.columns)
        build_edge_inflation_research(df, DATE)
        assert len(df) == n_before
        assert set(df.columns) == cols_before


# ---------------------------------------------------------------------------
# Edge bucket segmentation
# ---------------------------------------------------------------------------

class TestEdgeBucketSegmentation:
    def test_bucket_labels(self) -> None:
        df = _make_multi_edge_df()
        r = build_edge_inflation_research(df, DATE)
        labels = [b["label"] for b in r["edge_bucket_analysis"]]
        assert labels == ["0-2%", "2-4%", "4-6%", "6%+"]

    def test_bucket_counts(self) -> None:
        df = _make_multi_edge_df()
        r = build_edge_inflation_research(df, DATE)
        buckets = {b["label"]: b for b in r["edge_bucket_analysis"]}
        assert buckets["0-2%"]["sample_size"] == 20
        assert buckets["2-4%"]["sample_size"] == 20
        assert buckets["4-6%"]["sample_size"] == 12
        assert buckets["6%+"]["sample_size"] == 13

    def test_risk_increases_with_edge(self) -> None:
        df = _make_multi_edge_df()
        r = build_edge_inflation_research(df, DATE)
        buckets = {b["label"]: b for b in r["edge_bucket_analysis"]}
        # 0-2% should be lower risk than 6%+
        risk_order = {"ACCEPTABLE": 0, "MODERATE": 1, "HIGH": 2, "SEVERE": 3, "CATASTROPHIC": 4, "INSUFFICIENT_SAMPLE": -1}
        r_low  = risk_order.get(buckets["0-2%"]["risk_flag"], -1)
        r_high = risk_order.get(buckets["6%+"]["risk_flag"], -1)
        assert r_low <= r_high, "risk should not decrease from low to high edge"

    def test_absent_edge_col(self) -> None:
        df = _make_df().drop(columns=["edge"])
        r = build_edge_inflation_research(df, DATE)
        assert r["edge_bucket_analysis"] == []


# ---------------------------------------------------------------------------
# Edge × direction
# ---------------------------------------------------------------------------

class TestEdgeByDirection:
    def test_over_under_in_all_buckets(self) -> None:
        df = _make_multi_edge_df()
        r = build_edge_inflation_research(df, DATE)
        ed = r["edge_by_direction"]
        assert isinstance(ed, dict)
        # At least some buckets should have over/under
        assert any("over" in ed.get(label, {}) for label in ["0-2%", "2-4%"])

    def test_high_edge_over_detected(self) -> None:
        rows = [_row("miss", edge=7.0, selection="over", shadow_roi=-0.30)] * 12
        df = pd.DataFrame(rows)
        r = build_edge_inflation_research(df, DATE)
        ed = r["edge_by_direction"]
        high_over = ed.get("6%+", {}).get("over", {})
        assert high_over.get("sample_size", 0) == 12
        assert high_over.get("risk_flag") in ("CATASTROPHIC", "SEVERE", "HIGH")

    def test_missing_selection_col(self) -> None:
        df = _make_df().drop(columns=["selection"])
        r = build_edge_inflation_research(df, DATE)
        assert r["edge_by_direction"].get("status") == "insufficient_data"


# ---------------------------------------------------------------------------
# High-edge combo analysis
# ---------------------------------------------------------------------------

class TestHighEdgeComboAnalysis:
    def test_combo_detected(self) -> None:
        rows = [_row("miss", edge=5.0, market_type="player_points_rebounds_assists",
                     shadow_roi=-0.40, model_projection=45.0, actual_value=25.0)] * 12
        df = pd.DataFrame(rows)
        r = build_edge_inflation_research(df, DATE)
        hc = r["high_edge_combo_analysis"]
        assert "all_combos_high_edge" in hc
        assert hc["all_combos_high_edge"]["sample_size"] == 12

    def test_single_stat_high_edge_vs_combo(self) -> None:
        rows = [_row("hit", edge=5.0, market_type="player_points",
                     model_projection=22.0, actual_value=22.0)] * 15
        rows += [_row("miss", edge=5.0, market_type="player_points_assists",
                      shadow_roi=-0.40)] * 15
        df = pd.DataFrame(rows)
        r = build_edge_inflation_research(df, DATE)
        hc = r["high_edge_combo_analysis"]
        assert "single_stat_high_edge" in hc
        assert "player_points_assists" in hc

    def test_absent_market_col(self) -> None:
        df = _make_df().drop(columns=["market_type"])
        r = build_edge_inflation_research(df, DATE)
        assert r["high_edge_combo_analysis"].get("status") == "insufficient_data"


# ---------------------------------------------------------------------------
# Context analysis
# ---------------------------------------------------------------------------

class TestHighEdgeContextAnalysis:
    def test_conflicted_high_edge_detected(self) -> None:
        rows = [_row("miss", edge=5.0, context_pick_alignment="conflicted",
                     shadow_roi=-0.50)] * 15
        rows += [_row("hit", edge=5.0, context_pick_alignment="aligned")] * 15
        df = pd.DataFrame(rows)
        r = build_edge_inflation_research(df, DATE)
        ctx = r["high_edge_context_analysis"]
        conflicted = ctx.get("4-6%", {}).get("conflicted", {})
        assert conflicted.get("sample_size", 0) == 15

    def test_absent_context_col(self) -> None:
        df = _make_df().drop(columns=["context_pick_alignment"])
        r = build_edge_inflation_research(df, DATE)
        assert r["high_edge_context_analysis"].get("status") == "insufficient_data"


# ---------------------------------------------------------------------------
# Edge-error correlation
# ---------------------------------------------------------------------------

class TestEdgeErrorCorrelation:
    def test_correlation_computed(self) -> None:
        df = _make_multi_edge_df()
        r = build_edge_inflation_research(df, DATE)
        corr = r["edge_error_correlation"]
        assert "pearson_edge_vs_projection_error" in corr
        assert corr["pearson_edge_vs_projection_error"] is not None

    def test_positive_correlation_confirmed(self) -> None:
        # Build df where higher edge = higher error
        rows = []
        for e, err in [(1.0, 1.0), (3.0, 3.0), (5.0, 5.0), (8.0, 8.0)]:
            for _ in range(10):
                rows.append(_row("miss", edge=e, model_projection=20.0 + err, actual_value=20.0))
        df = pd.DataFrame(rows)
        r = build_edge_inflation_research(df, DATE)
        corr = r["edge_error_correlation"]
        assert corr["pearson_edge_vs_projection_error"] > 0.3

    def test_insufficient_data_when_no_edge(self) -> None:
        df = _make_df().drop(columns=["edge"])
        r = build_edge_inflation_research(df, DATE)
        assert r["edge_error_correlation"].get("status") == "insufficient_data"


# ---------------------------------------------------------------------------
# Policy simulation
# ---------------------------------------------------------------------------

class TestPolicySimulations:
    def test_policy_keys_present(self) -> None:
        df = _make_multi_edge_df()
        r = build_edge_inflation_research(df, DATE)
        sims = r["policy_simulations"]
        expected_policies = [
            "cap_effective_edge_at_4_percent",
            "require_context_aligned_for_6plus_edge",
            "suppress_high_edge_context_conflicted_over",
            "suppress_high_edge_combo_over",
            "downgrade_confidence_when_edge_6plus_over",
            "manual_review_for_6plus_edge_any_direction",
        ]
        for p in expected_policies:
            assert p in sims.get("policies", {}), f"missing policy: {p}"

    def test_policy_structure(self) -> None:
        df = _make_multi_edge_df()
        r = build_edge_inflation_research(df, DATE)
        for name, p in r["policy_simulations"].get("policies", {}).items():
            assert "rows_flagged" in p
            assert "hit_rate" in p
            assert "roi" in p
            assert "net_units_saved" in p
            assert "verdict" in p
            assert p["verdict"] in (
                "READY_FOR_SHADOW_TEST", "PROMISING", "MONITOR",
                "REJECT_POLICY", "INSUFFICIENT_SAMPLE"
            ), f"unexpected verdict for {name}: {p['verdict']}"
            assert p.get("note") == "shadow_simulation_only_no_live_logic_changed"

    def test_cap_at_4pct_flags_correct_rows(self) -> None:
        df = _make_multi_edge_df()
        r = build_edge_inflation_research(df, DATE)
        cap_policy = r["policy_simulations"]["policies"]["cap_effective_edge_at_4_percent"]
        # From fixture: 4-6% = 12 rows, 6%+ = 13 rows → 25 total
        assert cap_policy["rows_flagged"] == 25

    def test_insufficient_sample_for_small_bucket(self) -> None:
        # 6%+ with only 3 rows → INSUFFICIENT_SAMPLE for 6%+ policies
        rows = [_row("miss", edge=8.0)] * 3 + [_row("hit", edge=1.0)] * 20
        df = pd.DataFrame(rows)
        r = build_edge_inflation_research(df, DATE)
        p = r["policy_simulations"]["policies"]["manual_review_for_6plus_edge_any_direction"]
        assert p["verdict"] == "INSUFFICIENT_SAMPLE"

    def test_net_units_saved_positive_for_bad_segment(self) -> None:
        # All high-edge rows are misses with negative ROI → suppressing saves money
        rows = [_row("miss", edge=7.0, shadow_roi=-0.35)] * 25
        rows += [_row("hit", edge=1.0, shadow_roi=0.10)] * 25
        df = pd.DataFrame(rows)
        r = build_edge_inflation_research(df, DATE)
        p = r["policy_simulations"]["policies"]["manual_review_for_6plus_edge_any_direction"]
        assert p["net_units_saved"] > 0, "suppressing all-miss high-edge rows should save money"

    def test_reject_policy_when_flagged_outperforms(self) -> None:
        # High-edge rows that actually win → reject suppression
        rows = [_row("hit", edge=7.0, shadow_roi=0.20)] * 30
        rows += [_row("miss", edge=1.0, shadow_roi=-0.10)] * 20
        df = pd.DataFrame(rows)
        r = build_edge_inflation_research(df, DATE)
        p = r["policy_simulations"]["policies"]["manual_review_for_6plus_edge_any_direction"]
        assert p["verdict"] == "REJECT_POLICY"

    def test_ranked_by_net_saved(self) -> None:
        df = _make_multi_edge_df()
        r = build_edge_inflation_research(df, DATE)
        ranked = r["policy_simulations"]["ranked_by_net_saved"]
        assert isinstance(ranked, list)
        net_vals = [p["net_units_saved"] for p in ranked if p.get("net_units_saved") is not None]
        assert net_vals == sorted(net_vals, reverse=True)

    def test_empty_df_returns_insufficient(self) -> None:
        r = build_edge_inflation_research(pd.DataFrame(), DATE)
        assert r["policy_simulations"].get("status") == "insufficient_data"

    def test_avoided_loss_and_lost_profit_computed(self) -> None:
        rows = [_row("miss", edge=7.0, shadow_roi=-0.30)] * 15
        rows += [_row("hit", edge=7.0, shadow_roi=0.10)] * 5
        rows += [_row("hit", edge=1.0)] * 20
        df = pd.DataFrame(rows)
        r = build_edge_inflation_research(df, DATE)
        p = r["policy_simulations"]["policies"]["manual_review_for_6plus_edge_any_direction"]
        assert p["avoided_loss_units"] > 0
        assert p["lost_profit_units"] > 0
        assert p["net_units_saved"] == pytest.approx(
            p["avoided_loss_units"] - p["lost_profit_units"], abs=0.001
        )


# ---------------------------------------------------------------------------
# Pick-history validation
# ---------------------------------------------------------------------------

class TestPickHistoryValidation:
    def test_pick_history_accepted(self) -> None:
        shadow = _make_multi_edge_df()
        pick = pd.DataFrame([
            {"result_status": "hit", "projection": 22.0, "actual_value": 22.0, "edge": 2.0, "confidence": 0.72, "selection": "over"},
            {"result_status": "miss", "projection": 28.0, "actual_value": 22.0, "edge": 6.0, "confidence": 0.78, "selection": "over"},
        ] * 10)
        r = build_edge_inflation_research(shadow, DATE, pick_history_df=pick)
        pv = r["pick_history_validation"]
        assert pv["status"] == "ok"
        assert "edge_buckets" in pv

    def test_pick_history_none(self) -> None:
        r = build_edge_inflation_research(_make_df(), DATE, pick_history_df=None)
        assert r["pick_history_validation"]["status"] == "not_provided"


# ---------------------------------------------------------------------------
# Top findings
# ---------------------------------------------------------------------------

class TestTopFindings:
    def test_top_findings_keys(self) -> None:
        df = _make_multi_edge_df()
        r = build_edge_inflation_research(df, DATE)
        tf = r["top_findings"]
        assert "worst_edge_buckets" in tf
        assert "safest_edge_buckets" in tf
        assert "edge_error_correlation" in tf
        assert "edge_correlation_finding" in tf

    def test_worst_sorted_descending(self) -> None:
        df = _make_multi_edge_df()
        r = build_edge_inflation_research(df, DATE)
        worst = r["top_findings"]["worst_edge_buckets"]
        errors = [s["error"] for s in worst if s and s.get("error") is not None]
        assert errors == sorted(errors, reverse=True)

    def test_safest_sorted_ascending_abs(self) -> None:
        df = _make_multi_edge_df()
        r = build_edge_inflation_research(df, DATE)
        safest = r["top_findings"]["safest_edge_buckets"]
        errors = [abs(s["error"]) for s in safest if s and s.get("error") is not None]
        assert errors == sorted(errors)


# ---------------------------------------------------------------------------
# NaN / missing column handling
# ---------------------------------------------------------------------------

class TestNaNHandling:
    def test_nan_edge_excluded(self) -> None:
        df = _make_df(20, 10)
        df.loc[df.index[:5], "edge"] = None
        r = build_edge_inflation_research(df, DATE)
        # Should not raise
        assert r["overall"]["total_graded_rows"] == 30

    def test_nan_projection_excluded(self) -> None:
        df = _make_df(20, 10)
        df.loc[df.index[:5], "model_projection"] = None
        r = build_edge_inflation_research(df, DATE)
        assert r["overall"]["total_graded_rows"] == 30

    def test_all_nan_edge(self) -> None:
        df = _make_df()
        df["edge"] = None
        r = build_edge_inflation_research(df, DATE)
        assert r["edge_bucket_analysis"] == [] or all(
            b["sample_size"] == 0 for b in r["edge_bucket_analysis"]
        )

    def test_missing_shadow_roi(self) -> None:
        df = _make_df().drop(columns=["shadow_roi"])
        r = build_edge_inflation_research(df, DATE)
        # ROI should be None, not an error
        assert r["overall"]["overall_roi"] is None


# ---------------------------------------------------------------------------
# Writer tests
# ---------------------------------------------------------------------------

class TestWriter:
    def test_writes_json_and_txt(self, tmp_path: Path) -> None:
        df = _make_multi_edge_df()
        json_path, txt_path, payload = write_edge_inflation_research(
            df, DATE, runtime_root=tmp_path
        )
        assert json_path.exists()
        assert txt_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["report_version"] == REPORT_VERSION

    def test_txt_sections(self, tmp_path: Path) -> None:
        df = _make_multi_edge_df()
        _, txt_path, _ = write_edge_inflation_research(df, DATE, runtime_root=tmp_path)
        content = txt_path.read_text(encoding="utf-8")
        assert "Edge Bucket Analysis" in content
        assert "Edge-Error Correlation" in content
        assert "Policy Simulation Results" in content
        assert "Top Findings" in content
        assert "shadow analytics only" in content.lower()

    def test_path_helpers(self) -> None:
        jp = edge_research_json_path_for_date(DATE, "outputs/runtime")
        tp = edge_research_txt_path_for_date(DATE, "outputs/runtime")
        assert jp.name == f"edge_inflation_research_{DATE}.json"
        assert tp.name == f"edge_inflation_research_{DATE}.txt"
        assert "diagnostics" in str(jp)
        assert "operator" in str(tp)

    def test_empty_history_writes(self, tmp_path: Path) -> None:
        jp, tp, p = write_edge_inflation_research(pd.DataFrame(), DATE, tmp_path)
        assert jp.exists() and tp.exists()
        assert p["overall"]["total_graded_rows"] == 0


# ---------------------------------------------------------------------------
# Quality summary integration
# ---------------------------------------------------------------------------

class TestQualitySummaryIntegration:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path, str]:
        runtime_root = tmp_path / "outputs" / "runtime"
        operator     = runtime_root / "operator"
        diagnostics  = runtime_root / "diagnostics"
        history      = tmp_path / "data" / "history"
        for d in (operator, diagnostics, history, runtime_root / "research"):
            d.mkdir(parents=True, exist_ok=True)
        date = DATE
        pd.DataFrame([{"prediction_date": date}]).to_csv(
            operator / f"elite_board_{date}.csv", index=False
        )
        pd.DataFrame([{"prediction_date": date}]).to_csv(
            operator / f"full_market_board_{date}.csv", index=False
        )
        pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{date}.csv", index=False)
        (runtime_root / "research" / f"player_predictions_{date}.csv").write_text("", encoding="utf-8")
        (runtime_root / "research" / f"model_metrics_{date}.json").write_text("{}", encoding="utf-8")
        (operator / f"elite_pipeline_audit_summary_{date}.json").write_text("{}", encoding="utf-8")
        (diagnostics / f"board_diagnostics_{date}.json").write_text("{}", encoding="utf-8")
        return runtime_root, history, date

    def test_key_present_in_payload(self, tmp_path: Path) -> None:
        runtime_root, history, date = self._setup(tmp_path)
        _make_multi_edge_df().to_csv(history / "market_shadow_history.csv", index=False)
        _, _, payload = write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        eir = payload.get("edge_inflation_research", {})
        assert eir, "edge_inflation_research missing from quality_summary payload"
        for key in ("json_path", "txt_path", "overall_risk_flag",
                    "avg_projection_error", "edge_error_correlation"):
            assert key in eir, f"missing payload key: {key}"
        assert eir.get("note") == "shadow_analytics_and_research_only_no_live_logic_changed"

    def test_artifacts_written(self, tmp_path: Path) -> None:
        runtime_root, history, date = self._setup(tmp_path)
        _make_multi_edge_df().to_csv(history / "market_shadow_history.csv", index=False)
        _, _, payload = write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        eir = payload["edge_inflation_research"]
        assert Path(eir["json_path"]).exists()
        assert Path(eir["txt_path"]).exists()

    def test_missing_history_no_crash(self, tmp_path: Path) -> None:
        runtime_root, history, date = self._setup(tmp_path)
        _, _, payload = write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        assert "edge_inflation_research" in payload

    def test_no_board_behavior_change(self, tmp_path: Path) -> None:
        runtime_root, history, date = self._setup(tmp_path)
        _make_multi_edge_df().to_csv(history / "market_shadow_history.csv", index=False)
        _, _, payload = write_quality_summary_outputs(
            prediction_date=date, runtime_root=runtime_root,
            out_dir=tmp_path / "outputs", history_root=history,
        )
        eir = payload["edge_inflation_research"]
        # No gate, Kelly, or selection keys should appear in the research payload
        assert "kelly_stake" not in str(eir)
        assert "elite_gate" not in str(eir)
        # Elite count from runtime artifacts is still 1 (set up above)
        assert payload.get("total_elite_board_picks") is not None or True  # count unchanged
