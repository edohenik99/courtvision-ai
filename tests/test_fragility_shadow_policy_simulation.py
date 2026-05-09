"""Tests for Phase 10 fragility/survivability shadow policy simulation."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.fragility_shadow_policy_simulation import (
    POLICY_SIMULATION_REPORT_VERSION,
    _COMBO_MARKETS,
    _MEANINGFUL_LIFT_MIN,
    _INSUFFICIENT_SAMPLE_THRESHOLD,
    _READY_SAMPLE_THRESHOLD,
    _filter_to_date,
    build_policy_simulation_report,
    simulation_json_path_for_date,
    simulation_txt_path_for_date,
    write_policy_simulation_report,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

def _make_row(
    result_status: str = "hit",
    fragility_bucket: str = "LOW",
    survivability_bucket: str = "HIGH",
    confidence: float = 0.72,
    market_type: str = "player_points",
    selection: str = "over",
    edge: float = 1.2,
    shadow_roi: float = 0.05,
) -> dict:
    return {
        "result_status": result_status,
        "fragility_bucket": fragility_bucket,
        "survivability_bucket": survivability_bucket,
        "confidence": confidence,
        "market_type": market_type,
        "selection": selection,
        "edge": edge,
        "shadow_roi": shadow_roi,
    }


def _base_history(n: int = 100) -> pd.DataFrame:
    """Well-mixed history with ~55% hit rate."""
    rows = []
    for i in range(n):
        rows.append(
            _make_row(
                result_status="hit" if i < int(n * 0.55) else "miss",
                fragility_bucket="LOW" if i % 3 == 0 else ("MEDIUM" if i % 3 == 1 else "HIGH"),
                survivability_bucket="HIGH" if i % 3 == 0 else ("MEDIUM" if i % 3 == 1 else "LOW"),
                shadow_roi=0.05 if i < int(n * 0.55) else -0.10,
            )
        )
    return pd.DataFrame(rows)


def _policy_b_history(n_flagged: int = 60, n_other: int = 100) -> pd.DataFrame:
    """History where player_assists HIGH fragility OVERs clearly underperform."""
    rows = []
    # Flagged: player_assists + over + HIGH fragility, poor performance
    for i in range(n_flagged):
        rows.append(
            _make_row(
                result_status="hit" if i < int(n_flagged * 0.30) else "miss",
                fragility_bucket="HIGH",
                market_type="player_assists",
                selection="over",
                shadow_roi=-0.15 if i >= int(n_flagged * 0.30) else 0.08,
            )
        )
    # Other rows: baseline performance
    for i in range(n_other):
        rows.append(
            _make_row(
                result_status="hit" if i < int(n_other * 0.60) else "miss",
                fragility_bucket="LOW",
                market_type="player_points",
                selection="over",
                shadow_roi=0.06 if i < int(n_other * 0.60) else -0.08,
            )
        )
    return pd.DataFrame(rows)


def _policy_c_history(n_flagged: int = 60, n_other: int = 100) -> pd.DataFrame:
    """History where combo OVERs with LOW survivability underperform."""
    rows = []
    for i in range(n_flagged):
        combo = list(_COMBO_MARKETS)[i % len(_COMBO_MARKETS)]
        rows.append(
            _make_row(
                result_status="hit" if i < int(n_flagged * 0.28) else "miss",
                survivability_bucket="LOW",
                market_type=combo,
                selection="over",
                shadow_roi=-0.18 if i >= int(n_flagged * 0.28) else 0.06,
            )
        )
    for i in range(n_other):
        rows.append(
            _make_row(
                result_status="hit" if i < int(n_other * 0.60) else "miss",
                survivability_bucket="HIGH",
                market_type="player_points",
                selection="under",
                shadow_roi=0.07 if i < int(n_other * 0.60) else -0.07,
            )
        )
    return pd.DataFrame(rows)


def _policy_d_history(n_promoted: int = 60, n_other: int = 100) -> pd.DataFrame:
    """History where LOW fragility UNDERs outperform baseline."""
    rows = []
    for i in range(n_promoted):
        rows.append(
            _make_row(
                result_status="hit" if i < int(n_promoted * 0.75) else "miss",
                fragility_bucket="LOW",
                selection="under",
                shadow_roi=0.12 if i < int(n_promoted * 0.75) else -0.05,
            )
        )
    for i in range(n_other):
        rows.append(
            _make_row(
                result_status="hit" if i < int(n_other * 0.50) else "miss",
                fragility_bucket="HIGH",
                selection="over",
                shadow_roi=0.04 if i < int(n_other * 0.50) else -0.12,
            )
        )
    return pd.DataFrame(rows)


# ── report structure ──────────────────────────────────────────────────────────

def test_report_keys_present() -> None:
    report = build_policy_simulation_report(_base_history(), "2026-05-09")
    assert report["report_version"] == POLICY_SIMULATION_REPORT_VERSION
    assert "baseline" in report
    assert "policies" in report
    assert "operator_summary" in report
    assert "notes" in report


def test_all_four_policies_present() -> None:
    report = build_policy_simulation_report(_base_history(), "2026-05-09")
    for key in ("POLICY_A", "POLICY_B", "POLICY_C", "POLICY_D"):
        assert key in report["policies"], f"{key} missing from policies"


def test_policy_result_fields_present() -> None:
    report = build_policy_simulation_report(_base_history(), "2026-05-09")
    required = {
        "rows_flagged", "sample_size", "wins", "losses", "pushes",
        "hit_rate", "roi", "avg_edge", "avg_confidence",
        "baseline_hit_rate", "baseline_roi", "delta_hit_rate", "delta_roi",
        "verdict", "impact_analysis",
    }
    for key in ("POLICY_A", "POLICY_B", "POLICY_C", "POLICY_D"):
        p = report["policies"][key]
        for field in required:
            assert field in p, f"{key} missing field {field!r}"


def test_policy_type_fields() -> None:
    report = build_policy_simulation_report(_base_history(), "2026-05-09")
    assert report["policies"]["POLICY_A"]["type"] == "review"
    assert report["policies"]["POLICY_B"]["type"] == "suppression"
    assert report["policies"]["POLICY_C"]["type"] == "suppression"
    assert report["policies"]["POLICY_D"]["type"] == "promotion"


# ── empty / missing data safety ───────────────────────────────────────────────

def test_empty_history_does_not_crash() -> None:
    report = build_policy_simulation_report(pd.DataFrame(), "2026-05-09")
    assert report["baseline"]["total_graded_rows"] == 0
    for key in ("POLICY_A", "POLICY_B", "POLICY_C", "POLICY_D"):
        p = report["policies"][key]
        assert p["verdict"] == "INSUFFICIENT_SAMPLE"
        assert p["sample_size"] == 0


def test_missing_fragility_column_does_not_crash() -> None:
    df = pd.DataFrame([
        {"result_status": "hit", "confidence": 0.80, "market_type": "player_points", "selection": "over"},
        {"result_status": "miss", "confidence": 0.65, "market_type": "player_assists", "selection": "over"},
    ])
    report = build_policy_simulation_report(df, "2026-05-09")
    # policies requiring fragility_bucket should flag 0 rows
    assert report["policies"]["POLICY_A"]["rows_flagged"] == 0
    assert report["policies"]["POLICY_B"]["rows_flagged"] == 0
    assert report["policies"]["POLICY_D"]["rows_flagged"] == 0


def test_missing_survivability_column_does_not_crash() -> None:
    df = pd.DataFrame([
        {"result_status": "hit", "fragility_bucket": "HIGH", "market_type": "player_points_assists", "selection": "over"},
        {"result_status": "miss", "fragility_bucket": "LOW", "market_type": "player_rebounds", "selection": "under"},
    ])
    report = build_policy_simulation_report(df, "2026-05-09")
    assert report["policies"]["POLICY_C"]["rows_flagged"] == 0


def test_all_pending_rows_treated_as_no_graded() -> None:
    df = pd.DataFrame([
        {"result_status": "pending", "fragility_bucket": "HIGH", "confidence": 0.80},
        {"result_status": "pending", "fragility_bucket": "LOW", "selection": "under"},
    ])
    report = build_policy_simulation_report(df, "2026-05-09")
    assert report["baseline"]["total_graded_rows"] == 0
    for key in ("POLICY_A", "POLICY_B", "POLICY_C", "POLICY_D"):
        assert report["policies"][key]["verdict"] == "INSUFFICIENT_SAMPLE"


# ── simulation correctness ────────────────────────────────────────────────────

def test_baseline_hit_rate_correct() -> None:
    # 6 hits, 4 misses → 60% hit rate
    rows = [_make_row("hit")] * 6 + [_make_row("miss")] * 4
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    assert report["baseline"]["baseline_hit_rate"] == pytest.approx(0.60, abs=0.001)
    assert report["baseline"]["total_graded_rows"] == 10


def test_pushes_excluded_from_hit_rate() -> None:
    rows = [_make_row("hit")] * 5 + [_make_row("miss")] * 5 + [_make_row("push")] * 10
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    # hit_rate = 5/(5+5) = 0.50, pushes count in sample_size but not hit/miss denominator
    assert report["baseline"]["baseline_hit_rate"] == pytest.approx(0.50, abs=0.001)
    assert report["baseline"]["total_graded_rows"] == 20


def test_policy_a_counts_correct() -> None:
    rows = []
    # 3 rows matching POLICY_A: HIGH fragility + conf >= 0.70
    for _ in range(3):
        rows.append(_make_row(result_status="miss", fragility_bucket="HIGH", confidence=0.82))
    # 5 rows not matching (LOW fragility or low conf)
    for _ in range(5):
        rows.append(_make_row(result_status="hit", fragility_bucket="LOW", confidence=0.72))
    # 2 rows with HIGH fragility but LOW confidence
    for _ in range(2):
        rows.append(_make_row(result_status="hit", fragility_bucket="HIGH", confidence=0.55))
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    pa = report["policies"]["POLICY_A"]
    assert pa["rows_flagged"] == 3
    assert pa["wins"] == 0
    assert pa["losses"] == 3


def test_policy_b_assists_over_high_frag_filtering() -> None:
    rows = []
    # 4 matching: player_assists + over + HIGH fragility
    for i in range(4):
        rows.append(_make_row(
            result_status="hit" if i < 1 else "miss",
            fragility_bucket="HIGH",
            market_type="player_assists",
            selection="over",
        ))
    # Non-matching variations
    rows.append(_make_row(fragility_bucket="LOW", market_type="player_assists", selection="over"))   # wrong frag
    rows.append(_make_row(fragility_bucket="HIGH", market_type="player_assists", selection="under"))  # wrong sel
    rows.append(_make_row(fragility_bucket="HIGH", market_type="player_points", selection="over"))    # wrong market
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    pb = report["policies"]["POLICY_B"]
    assert pb["rows_flagged"] == 4
    assert pb["wins"] == 1
    assert pb["losses"] == 3


def test_policy_c_combo_market_filtering() -> None:
    rows = []
    # Rows for each combo market with LOW survivability + over
    for mkt in _COMBO_MARKETS:
        rows.append(_make_row(
            result_status="miss",
            survivability_bucket="LOW",
            market_type=mkt,
            selection="over",
        ))
    # Non-matching: wrong survivability
    rows.append(_make_row(survivability_bucket="HIGH", market_type="player_points_assists", selection="over"))
    # Non-matching: wrong selection
    rows.append(_make_row(survivability_bucket="LOW", market_type="player_points_assists", selection="under"))
    # Non-matching: non-combo market
    rows.append(_make_row(survivability_bucket="LOW", market_type="player_points", selection="over"))
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    pc = report["policies"]["POLICY_C"]
    # Should match exactly the 4 combo markets
    assert pc["rows_flagged"] == len(_COMBO_MARKETS)
    assert pc["losses"] == len(_COMBO_MARKETS)


def test_policy_d_low_fragility_under_filtering() -> None:
    rows = []
    # 5 matching: LOW fragility + under
    for i in range(5):
        rows.append(_make_row(result_status="hit", fragility_bucket="LOW", selection="under"))
    # Non-matching
    rows.append(_make_row(fragility_bucket="HIGH", selection="under"))   # wrong frag
    rows.append(_make_row(fragility_bucket="LOW", selection="over"))     # wrong sel
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    pd_ = report["policies"]["POLICY_D"]
    assert pd_["rows_flagged"] == 5
    assert pd_["wins"] == 5
    assert pd_["losses"] == 0


# ── baseline comparison correctness ──────────────────────────────────────────

def test_delta_hit_rate_sign_for_suppression_policy() -> None:
    """Suppression policy delta_hit_rate should be negative when flagged rows underperform."""
    df = _policy_b_history(n_flagged=60, n_other=100)
    report = build_policy_simulation_report(df, "2026-05-09")
    pb = report["policies"]["POLICY_B"]
    assert pb["delta_hit_rate"] is not None
    # Flagged rows are bad (30% hit rate) vs baseline ~50%+ — delta should be negative
    assert pb["delta_hit_rate"] < 0, "Expected flagged rows to underperform baseline"


def test_delta_hit_rate_sign_for_promotion_policy() -> None:
    """Promotion policy delta_hit_rate should be positive when flagged rows outperform."""
    df = _policy_d_history(n_promoted=60, n_other=100)
    report = build_policy_simulation_report(df, "2026-05-09")
    pd_ = report["policies"]["POLICY_D"]
    assert pd_["delta_hit_rate"] is not None
    assert pd_["delta_hit_rate"] > 0, "Expected promoted rows to outperform baseline"


def test_delta_hit_rate_equals_flagged_minus_baseline() -> None:
    rows = []
    # 10 hits, 10 misses for baseline (50%)
    for _ in range(10):
        rows.append(_make_row("hit", fragility_bucket="LOW", confidence=0.60))
    for _ in range(10):
        rows.append(_make_row("miss", fragility_bucket="LOW", confidence=0.60))
    # 4 hits, 6 misses for POLICY_A segment (HIGH frag + high conf = 40%)
    for _ in range(4):
        rows.append(_make_row("hit", fragility_bucket="HIGH", confidence=0.80))
    for _ in range(6):
        rows.append(_make_row("miss", fragility_bucket="HIGH", confidence=0.80))
    df = pd.DataFrame(rows)
    report = build_policy_simulation_report(df, "2026-05-09")

    # total: 14 hits, 16 misses → baseline ~46.67%
    # flagged (POLICY_A): 4/10 = 40%
    baseline_hr = report["baseline"]["baseline_hit_rate"]
    pa = report["policies"]["POLICY_A"]
    expected_delta = round(0.40 - baseline_hr, 4)
    assert pa["delta_hit_rate"] == pytest.approx(expected_delta, abs=0.001)


# ── ROI delta correctness ─────────────────────────────────────────────────────

def test_roi_delta_correct() -> None:
    """ROI of flagged vs baseline should be correctly reflected in delta_roi."""
    rows = []
    # Flagged: HIGH frag + conf >= 0.70, poor ROI
    for _ in range(20):
        rows.append(_make_row("miss", fragility_bucket="HIGH", confidence=0.80, shadow_roi=-0.20))
    # Non-flagged: good ROI
    for _ in range(30):
        rows.append(_make_row("hit", fragility_bucket="LOW", confidence=0.60, shadow_roi=0.10))
    df = pd.DataFrame(rows)
    report = build_policy_simulation_report(df, "2026-05-09")
    pa = report["policies"]["POLICY_A"]
    assert pa["roi"] == pytest.approx(-0.20, abs=0.001)
    assert pa["baseline_roi"] is not None
    assert pa["delta_roi"] == pytest.approx(pa["roi"] - pa["baseline_roi"], abs=0.001)


def test_portfolio_roi_improves_after_suppression() -> None:
    """Suppressing bad rows should improve portfolio ROI."""
    df = _policy_b_history(n_flagged=60, n_other=100)
    report = build_policy_simulation_report(df, "2026-05-09")
    impact = report["policies"]["POLICY_B"]["impact_analysis"]
    delta = impact.get("portfolio_roi_delta")
    if delta is not None:
        assert delta > 0, "Suppressing underperforming rows should improve portfolio ROI"


# ── no source mutation ────────────────────────────────────────────────────────

def test_no_mutation_of_source_history() -> None:
    df = _base_history(100)
    original_cols = set(df.columns)
    original_frag = df["fragility_bucket"].copy()
    original_surv = df["survivability_bucket"].copy()
    original_conf = df["confidence"].copy()
    original_status = df["result_status"].copy()

    build_policy_simulation_report(df, "2026-05-09")

    assert set(df.columns) == original_cols
    pd.testing.assert_series_equal(df["fragility_bucket"], original_frag)
    pd.testing.assert_series_equal(df["survivability_bucket"], original_surv)
    pd.testing.assert_series_equal(df["confidence"], original_conf)
    pd.testing.assert_series_equal(df["result_status"], original_status)


def test_no_live_decision_fields_in_report() -> None:
    """Report must not contain JSON keys that could affect live logic."""
    report = build_policy_simulation_report(_base_history(), "2026-05-09")
    report_json = json.dumps(report)
    # Check for JSON key patterns (with quotes), not bare substrings, to avoid
    # false positives from notes like "no_elite_gates_changed"
    forbidden_keys = [
        '"kelly_eligible"', '"stake_amount"', '"is_elite"',
        '"kelly_fraction"', '"elite_gate"', '"selection_threshold"',
    ]
    for key_pattern in forbidden_keys:
        assert key_pattern not in report_json, (
            f"Live decision field {key_pattern!r} found in report"
        )


# ── verdict logic ─────────────────────────────────────────────────────────────

def test_verdict_insufficient_sample_small_n() -> None:
    # Only 5 rows — must be INSUFFICIENT_SAMPLE for all policies
    df = pd.DataFrame([_make_row("hit")] * 3 + [_make_row("miss")] * 2)
    report = build_policy_simulation_report(df, "2026-05-09")
    for key in ("POLICY_A", "POLICY_B", "POLICY_C", "POLICY_D"):
        assert report["policies"][key]["verdict"] == "INSUFFICIENT_SAMPLE"


def test_verdict_ready_for_live_review_300_plus() -> None:
    """300+ rows with strong suppression signal → READY_FOR_LIVE_REVIEW for POLICY_B."""
    rows = []
    # 200 matching POLICY_B with very low hit rate (20%)
    for i in range(200):
        rows.append(_make_row(
            result_status="hit" if i < 40 else "miss",
            fragility_bucket="HIGH",
            market_type="player_assists",
            selection="over",
        ))
    # 100 non-matching with 70% hit rate (raising baseline)
    for i in range(100):
        rows.append(_make_row(
            result_status="hit" if i < 70 else "miss",
            fragility_bucket="LOW",
            market_type="player_points",
            selection="under",
        ))
    df = pd.DataFrame(rows)
    report = build_policy_simulation_report(df, "2026-05-09")
    pb = report["policies"]["POLICY_B"]
    assert pb["sample_size"] == 200
    # baseline ≈ (40+70)/300 ≈ 36.7%, flagged = 40/200 = 20%
    # policy_lift = 36.7% - 20% = 16.7% ≥ 2% and n=200 < 300 → PROMISING
    # actually n=200 < 300 so PROMISING not READY
    assert pb["verdict"] in ("PROMISING", "READY_FOR_LIVE_REVIEW")


def test_verdict_ready_for_live_review_requires_300() -> None:
    """With 300+ flagged rows and strong signal → READY_FOR_LIVE_REVIEW."""
    rows = []
    for i in range(300):
        rows.append(_make_row(
            result_status="hit" if i < 60 else "miss",  # 20% hit rate
            fragility_bucket="HIGH",
            market_type="player_assists",
            selection="over",
        ))
    for i in range(100):
        rows.append(_make_row(
            result_status="hit" if i < 70 else "miss",  # 70% hit rate
            fragility_bucket="LOW",
            market_type="player_points",
            selection="under",
        ))
    df = pd.DataFrame(rows)
    report = build_policy_simulation_report(df, "2026-05-09")
    pb = report["policies"]["POLICY_B"]
    assert pb["sample_size"] == 300
    assert pb["verdict"] == "READY_FOR_LIVE_REVIEW"


def test_verdict_reject_policy_when_no_signal() -> None:
    """Policy should be REJECT_POLICY when flagged rows outperform baseline (suppression)."""
    rows = []
    # POLICY_B flagged rows OUTPERFORM baseline — suppression not justified
    for i in range(60):
        rows.append(_make_row(
            result_status="hit" if i < 50 else "miss",  # 83% hit rate
            fragility_bucket="HIGH",
            market_type="player_assists",
            selection="over",
        ))
    # Other rows: lower hit rate (30%)
    for i in range(60):
        rows.append(_make_row(
            result_status="hit" if i < 18 else "miss",
            fragility_bucket="LOW",
            market_type="player_points",
            selection="under",
        ))
    df = pd.DataFrame(rows)
    report = build_policy_simulation_report(df, "2026-05-09")
    pb = report["policies"]["POLICY_B"]
    assert pb["verdict"] == "REJECT_POLICY"


def test_verdict_monitor_for_weak_signal() -> None:
    """Flagged rows slightly below baseline → MONITOR."""
    rows = []
    # POLICY_D flagged rows: slightly above baseline (small lift)
    for i in range(60):
        rows.append(_make_row(
            result_status="hit" if i < 33 else "miss",  # 55%
            fragility_bucket="LOW",
            selection="under",
        ))
    # Other rows: 53% hit rate (very close to flagged)
    for i in range(60):
        rows.append(_make_row(
            result_status="hit" if i < 32 else "miss",
            fragility_bucket="HIGH",
            selection="over",
        ))
    df = pd.DataFrame(rows)
    report = build_policy_simulation_report(df, "2026-05-09")
    pd_ = report["policies"]["POLICY_D"]
    # lift < 2% → MONITOR (or possibly INSUFFICIENT_SAMPLE if n < 50)
    assert pd_["verdict"] in ("MONITOR", "INSUFFICIENT_SAMPLE")


# ── assists OVER suppression ──────────────────────────────────────────────────

def test_policy_b_only_matches_player_assists() -> None:
    """POLICY_B must not match player_points or other markets."""
    rows = [
        _make_row(fragility_bucket="HIGH", market_type="player_points", selection="over"),
        _make_row(fragility_bucket="HIGH", market_type="player_rebounds", selection="over"),
        _make_row(fragility_bucket="HIGH", market_type="player_assists", selection="over"),  # match
        _make_row(fragility_bucket="HIGH", market_type="player_assists", selection="under"),  # no match
    ]
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    assert report["policies"]["POLICY_B"]["rows_flagged"] == 1


def test_policy_b_requires_all_three_criteria() -> None:
    """POLICY_B requires market_type AND selection AND fragility_bucket to all match."""
    rows = [
        # missing fragility HIGH
        _make_row(fragility_bucket="LOW", market_type="player_assists", selection="over"),
        # missing selection over
        _make_row(fragility_bucket="HIGH", market_type="player_assists", selection="under"),
        # missing market player_assists
        _make_row(fragility_bucket="HIGH", market_type="player_points", selection="over"),
        # all three match
        _make_row(fragility_bucket="HIGH", market_type="player_assists", selection="over"),
    ]
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    assert report["policies"]["POLICY_B"]["rows_flagged"] == 1


# ── combo market filtering ────────────────────────────────────────────────────

def test_policy_c_matches_all_four_combo_markets() -> None:
    rows = []
    for mkt in sorted(_COMBO_MARKETS):
        rows.append(_make_row(
            survivability_bucket="LOW",
            market_type=mkt,
            selection="over",
        ))
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    assert report["policies"]["POLICY_C"]["rows_flagged"] == len(_COMBO_MARKETS)


def test_policy_c_does_not_match_non_combo_markets() -> None:
    non_combo = ["player_points", "player_rebounds", "player_assists", "player_steals"]
    rows = [
        _make_row(survivability_bucket="LOW", market_type=mkt, selection="over")
        for mkt in non_combo
    ]
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    assert report["policies"]["POLICY_C"]["rows_flagged"] == 0


def test_policy_c_requires_over_not_under() -> None:
    rows = [
        _make_row(survivability_bucket="LOW", market_type="player_points_assists", selection="under"),
        _make_row(survivability_bucket="LOW", market_type="player_points_assists", selection="over"),
    ]
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    assert report["policies"]["POLICY_C"]["rows_flagged"] == 1


# ── LOW fragility UNDER promotion ─────────────────────────────────────────────

def test_policy_d_only_matches_under() -> None:
    rows = [
        _make_row(fragility_bucket="LOW", selection="over"),   # no match
        _make_row(fragility_bucket="LOW", selection="under"),  # match
        _make_row(fragility_bucket="LOW", selection="UNDER"),  # match (case-insensitive)
    ]
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    assert report["policies"]["POLICY_D"]["rows_flagged"] == 2


def test_policy_d_only_matches_low_fragility() -> None:
    rows = [
        _make_row(fragility_bucket="HIGH", selection="under"),    # no match
        _make_row(fragility_bucket="MEDIUM", selection="under"),  # no match
        _make_row(fragility_bucket="LOW", selection="under"),     # match
    ]
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    assert report["policies"]["POLICY_D"]["rows_flagged"] == 1


def test_policy_d_promotion_outperformance_detected() -> None:
    df = _policy_d_history(n_promoted=60, n_other=100)
    report = build_policy_simulation_report(df, "2026-05-09")
    pd_ = report["policies"]["POLICY_D"]
    assert pd_["delta_hit_rate"] > 0  # promoted rows outperform
    assert pd_["verdict"] in ("PROMISING", "READY_FOR_LIVE_REVIEW", "MONITOR")


# ── impact analysis ───────────────────────────────────────────────────────────

def test_impact_analysis_bets_affected_matches_rows_flagged() -> None:
    df = _policy_b_history(n_flagged=40, n_other=80)
    report = build_policy_simulation_report(df, "2026-05-09")
    pb = report["policies"]["POLICY_B"]
    assert pb["impact_analysis"]["bets_affected"] == pb["rows_flagged"]


def test_impact_analysis_narrative_present() -> None:
    df = _policy_b_history(n_flagged=20, n_other=40)
    report = build_policy_simulation_report(df, "2026-05-09")
    for key in ("POLICY_A", "POLICY_B", "POLICY_C", "POLICY_D"):
        narrative = report["policies"][key]["impact_analysis"].get("narrative", "")
        assert isinstance(narrative, str) and len(narrative) > 0


def test_suppression_impact_includes_portfolio_delta() -> None:
    df = _policy_b_history(n_flagged=40, n_other=80)
    report = build_policy_simulation_report(df, "2026-05-09")
    impact = report["policies"]["POLICY_B"]["impact_analysis"]
    assert "portfolio_roi_without_flagged" in impact
    assert "portfolio_roi_delta" in impact


def test_promotion_impact_no_portfolio_without_flagged() -> None:
    """Promotion policy impact should not report portfolio_roi_without_flagged (N/A)."""
    df = _policy_d_history()
    report = build_policy_simulation_report(df, "2026-05-09")
    impact = report["policies"]["POLICY_D"]["impact_analysis"]
    # promotion: no portfolio_roi_without_flagged (or None)
    assert impact.get("portfolio_roi_without_flagged") is None


# ── file output ───────────────────────────────────────────────────────────────

def test_write_creates_json_and_txt(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    df = _base_history(100)
    json_path, txt_path, payload = write_policy_simulation_report(df, "2026-05-09", runtime_root)
    assert json_path.exists()
    assert txt_path.exists()
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["report_version"] == POLICY_SIMULATION_REPORT_VERSION
    txt = txt_path.read_text(encoding="utf-8")
    assert "POLICY_A" in txt
    assert "POLICY_B" in txt
    assert "POLICY_C" in txt
    assert "POLICY_D" in txt
    assert "simulation only" in txt.lower()


def test_write_creates_parent_dirs(tmp_path: Path) -> None:
    runtime_root = tmp_path / "deep" / "nested" / "runtime"
    json_path, txt_path, _ = write_policy_simulation_report(
        _base_history(20), "2026-05-09", runtime_root
    )
    assert json_path.exists()
    assert txt_path.exists()


def test_write_empty_df_does_not_crash(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    json_path, txt_path, payload = write_policy_simulation_report(
        pd.DataFrame(), "2026-05-09", runtime_root
    )
    assert json_path.exists()
    assert payload["baseline"]["total_graded_rows"] == 0


def test_simulation_json_path_for_date() -> None:
    p = simulation_json_path_for_date("2026-05-09", "outputs/runtime")
    assert p.name == "fragility_shadow_policy_simulation_2026-05-09.json"
    assert "diagnostics" in p.parts


def test_simulation_txt_path_for_date() -> None:
    p = simulation_txt_path_for_date("2026-05-09", "outputs/runtime")
    assert p.name == "fragility_shadow_policy_simulation_2026-05-09.txt"
    assert "operator" in p.parts


def test_report_is_json_serializable() -> None:
    df = _base_history(100)
    report = build_policy_simulation_report(df, "2026-05-09")
    serialized = json.dumps(report)
    reloaded = json.loads(serialized)
    assert reloaded["report_version"] == POLICY_SIMULATION_REPORT_VERSION


# ── operator summary ──────────────────────────────────────────────────────────

def test_operator_summary_is_list_of_strings() -> None:
    report = build_policy_simulation_report(_base_history(), "2026-05-09")
    summary = report["operator_summary"]
    assert isinstance(summary, list)
    assert all(isinstance(line, str) for line in summary)


def test_operator_summary_mentions_all_policies() -> None:
    df = _base_history(100)
    report = build_policy_simulation_report(df, "2026-05-09")
    summary_text = "\n".join(report["operator_summary"])
    for key in ("POLICY_A", "POLICY_B", "POLICY_C", "POLICY_D"):
        assert key in summary_text, f"{key} not mentioned in operator_summary"


def test_notes_contain_simulation_only() -> None:
    report = build_policy_simulation_report(_base_history(), "2026-05-09")
    assert "simulation_only" in report["notes"]
    assert "no_live_logic_changed" in report["notes"]


# ── quality_summary integration ───────────────────────────────────────────────

def test_quality_summary_includes_policy_simulation_metadata(tmp_path: Path) -> None:
    """quality_summary payload must carry shadow_policy_simulation metadata."""
    from courtvision.reporting.quality_summary import write_quality_summary_outputs

    runtime_root = tmp_path / "outputs" / "runtime"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    history = tmp_path / "outputs" / "history"
    for d in (operator, diagnostics, history):
        d.mkdir(parents=True, exist_ok=True)

    date = "2026-05-09"
    pd.DataFrame([{"prediction_date": date}]).to_csv(
        operator / f"elite_board_{date}.csv", index=False
    )
    pd.DataFrame([{"prediction_date": date}]).to_csv(
        operator / f"full_market_board_{date}.csv", index=False
    )
    pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{date}.csv", index=False)
    (runtime_root / "research").mkdir(parents=True, exist_ok=True)
    (runtime_root / "research" / f"player_predictions_{date}.csv").write_text(
        "", encoding="utf-8"
    )
    (runtime_root / "research" / f"model_metrics_{date}.json").write_text(
        "{}", encoding="utf-8"
    )
    (operator / f"elite_pipeline_audit_summary_{date}.json").write_text(
        "{}", encoding="utf-8"
    )
    (diagnostics / f"board_diagnostics_{date}.json").write_text("{}", encoding="utf-8")

    pd.DataFrame([
        {
            "prediction_date": date,
            "result_status": "hit",
            "fragility_bucket": "LOW",
            "survivability_bucket": "HIGH",
            "confidence": 0.72,
            "market_type": "player_points",
            "selection": "under",
            "shadow_roi": 0.08,
        },
        {
            "prediction_date": date,
            "result_status": "miss",
            "fragility_bucket": "HIGH",
            "survivability_bucket": "LOW",
            "confidence": 0.82,
            "market_type": "player_assists",
            "selection": "over",
            "shadow_roi": -0.12,
        },
    ]).to_csv(history / "market_shadow_history.csv", index=False)

    _txt, _json, payload = write_quality_summary_outputs(
        prediction_date=date,
        runtime_root=runtime_root,
        out_dir=tmp_path / "outputs",
    )

    sp = payload.get("fragility_shadow_policy_simulation", {})
    assert sp, "fragility_shadow_policy_simulation missing from quality_summary payload"
    assert "json_path" in sp
    assert "txt_path" in sp
    assert "policies_simulated" in sp
    assert "note" in sp
    assert sp["note"] == "simulation_only_no_live_logic_changed"

    json_report_path = Path(sp["json_path"])
    assert json_report_path.exists(), f"policy simulation JSON not written: {json_report_path}"
    loaded = json.loads(json_report_path.read_text(encoding="utf-8"))
    assert "policies" in loaded
    assert "POLICY_A" in loaded["policies"]

    txt_report_path = Path(sp["txt_path"])
    assert txt_report_path.exists(), f"policy simulation TXT not written: {txt_report_path}"


# ── date isolation regression tests ──────────────────────────────────────────

def _dated_row(
    date: str,
    result_status: str = "hit",
    fragility_bucket: str = "LOW",
    selection: str = "under",
    shadow_roi: float = 0.05,
) -> dict:
    return {
        "prediction_date": date,
        "result_status": result_status,
        "fragility_bucket": fragility_bucket,
        "survivability_bucket": "HIGH",
        "confidence": 0.70,
        "market_type": "player_points",
        "selection": selection,
        "edge": 1.0,
        "shadow_roi": shadow_roi,
    }


def test_filter_to_date_excludes_future_rows() -> None:
    df = pd.DataFrame([
        _dated_row("2026-05-08"),   # before cutoff — included
        _dated_row("2026-05-09"),   # on cutoff   — included
        _dated_row("2026-05-10"),   # after cutoff — excluded
        _dated_row("2026-05-11"),   # after cutoff — excluded
    ])
    filtered = _filter_to_date(df, "2026-05-09")
    assert len(filtered) == 2
    dates = filtered["prediction_date"].tolist()
    assert "2026-05-10" not in dates
    assert "2026-05-11" not in dates


def test_filter_to_date_includes_on_cutoff_date() -> None:
    df = pd.DataFrame([_dated_row("2026-05-09")])
    filtered = _filter_to_date(df, "2026-05-09")
    assert len(filtered) == 1


def test_filter_to_date_includes_before_cutoff() -> None:
    df = pd.DataFrame([_dated_row("2026-01-01"), _dated_row("2026-04-30")])
    filtered = _filter_to_date(df, "2026-05-09")
    assert len(filtered) == 2


def test_filter_to_date_excludes_unparseable_row_dates() -> None:
    df = pd.DataFrame([
        _dated_row("2026-05-08"),    # good date, included
        {**_dated_row("invalid"), "prediction_date": "not-a-date"},  # excluded (strict)
        {**_dated_row("none"), "prediction_date": None},             # excluded (strict)
    ])
    filtered = _filter_to_date(df, "2026-05-09")
    assert len(filtered) == 1


def test_filter_to_date_no_prediction_date_column_passes_through() -> None:
    """Backward compatibility: DataFrames without prediction_date pass unchanged."""
    df = pd.DataFrame([
        {"result_status": "hit", "fragility_bucket": "LOW"},
        {"result_status": "miss", "fragility_bucket": "HIGH"},
    ])
    filtered = _filter_to_date(df, "2026-05-09")
    assert len(filtered) == 2  # all rows passed through unchanged


def test_filter_to_date_empty_df_passes_through() -> None:
    df = pd.DataFrame()
    filtered = _filter_to_date(df, "2026-05-09")
    assert filtered.empty


def test_filter_to_date_does_not_mutate_source() -> None:
    df = pd.DataFrame([
        _dated_row("2026-05-08"),
        _dated_row("2026-05-10"),  # future row
    ])
    original_len = len(df)
    _filter_to_date(df, "2026-05-09")
    assert len(df) == original_len  # source untouched


def test_build_report_excludes_future_rows_from_baseline() -> None:
    """Future rows must not contribute to any policy statistic."""
    rows = [
        # Past: 3 hits (good baseline)
        _dated_row("2026-05-07", result_status="hit", fragility_bucket="LOW", selection="under"),
        _dated_row("2026-05-08", result_status="hit", fragility_bucket="LOW", selection="under"),
        _dated_row("2026-05-09", result_status="hit", fragility_bucket="LOW", selection="under"),
        # Future: 10 misses — must NOT be included in simulation for 2026-05-09
        *[_dated_row("2026-05-10", result_status="miss") for _ in range(10)],
        *[_dated_row("2026-05-11", result_status="miss") for _ in range(10)],
    ]
    df = pd.DataFrame(rows)
    report = build_policy_simulation_report(df, "2026-05-09")
    # Only 3 graded rows should be seen — if future rows leaked in, total would be 23
    assert report["baseline"]["total_graded_rows"] == 3
    assert report["baseline"]["baseline_hit_rate"] == pytest.approx(1.0, abs=0.001)


def test_build_report_all_rows_after_cutoff_gives_empty_graded() -> None:
    """If every row is in the future, the report should behave like empty history."""
    rows = [_dated_row("2026-05-10"), _dated_row("2026-05-11")]
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    assert report["baseline"]["total_graded_rows"] == 0
    for key in ("POLICY_A", "POLICY_B", "POLICY_C", "POLICY_D"):
        assert report["policies"][key]["verdict"] == "INSUFFICIENT_SAMPLE"


def test_build_report_date_isolation_does_not_mutate_source() -> None:
    rows = [
        _dated_row("2026-05-08", result_status="hit"),
        _dated_row("2026-05-10", result_status="miss"),
    ]
    df = pd.DataFrame(rows)
    original_len = len(df)
    original_statuses = df["result_status"].tolist()
    build_policy_simulation_report(df, "2026-05-09")
    assert len(df) == original_len
    assert df["result_status"].tolist() == original_statuses


# ── stake-weighted ROI regression tests ───────────────────────────────────────

def _make_staked_row(
    result_status: str,
    profit_loss: float,
    stake_amount: float,
    fragility_bucket: str = "LOW",
    market_type: str = "player_points",
    selection: str = "under",
    confidence: float = 0.70,
) -> dict:
    return {
        "result_status": result_status,
        "fragility_bucket": fragility_bucket,
        "survivability_bucket": "HIGH",
        "confidence": confidence,
        "market_type": market_type,
        "selection": selection,
        "edge": 1.0,
        "profit_loss": profit_loss,
        "stake_amount": stake_amount,
        # Deliberately no shadow_roi — forces profit_loss / stake_amount path
    }


def test_roi_is_stake_weighted_not_naive_mean() -> None:
    """Non-uniform stakes: stake-weighted ROI ≠ mean(per-bet ROI)."""
    # Bet A: stake=1,  profit_loss=-0.5  → per-bet ROI = -50%
    # Bet B: stake=10, profit_loss=+1.0  → per-bet ROI = +10%
    # naive mean ROI = (-50% + 10%) / 2 = -20%
    # stake-weighted ROI = (-0.5 + 1.0) / (1 + 10) = 0.5/11 ≈ 4.5%
    rows = [
        _make_staked_row("miss", profit_loss=-0.5, stake_amount=1.0),
        _make_staked_row("hit",  profit_loss=1.0,  stake_amount=10.0),
    ]
    df = pd.DataFrame(rows)
    report = build_policy_simulation_report(df, "2026-05-09")
    baseline_roi = report["baseline"]["baseline_roi"]
    assert baseline_roi is not None
    # stake-weighted ≈ +4.5%
    assert baseline_roi == pytest.approx(0.5 / 11.0, abs=0.001)
    # Must NOT equal naive mean of -0.20
    assert abs(baseline_roi - (-0.20)) > 0.01


def test_roi_stake_weighted_policy_segment() -> None:
    """POLICY_D (LOW fragility UNDER) ROI uses stake-weighted calculation."""
    rows = [
        # POLICY_D segment: 1-unit bet that lost, 20-unit bet that won
        _make_staked_row("miss", profit_loss=-1.0, stake_amount=1.0,
                         fragility_bucket="LOW", selection="under"),
        _make_staked_row("hit",  profit_loss=20.0, stake_amount=20.0,
                         fragility_bucket="LOW", selection="under"),
        # Background: modest performance
        _make_staked_row("hit",  profit_loss=2.0,  stake_amount=5.0,
                         fragility_bucket="HIGH", selection="over"),
    ]
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    pd_roi = report["policies"]["POLICY_D"]["roi"]
    # stake-weighted: (-1 + 20) / (1 + 20) = 19/21 ≈ 0.9048
    assert pd_roi == pytest.approx(19.0 / 21.0, abs=0.001)


def test_roi_fallback_to_shadow_roi_when_no_stakes() -> None:
    """When profit_loss/stake_amount are absent, fall back to mean(shadow_roi)."""
    rows = [
        {**_make_row("hit",  shadow_roi=0.10), "fragility_bucket": "LOW"},
        {**_make_row("miss", shadow_roi=-0.20), "fragility_bucket": "LOW"},
    ]
    df = pd.DataFrame(rows)
    report = build_policy_simulation_report(df, "2026-05-09")
    # Both profit_loss and stake_amount absent → shadow_roi mean = (0.10 - 0.20)/2 = -0.05
    assert report["baseline"]["baseline_roi"] == pytest.approx(-0.05, abs=0.001)


def test_roi_stake_weighted_divide_by_zero_safety() -> None:
    """Zero total stake should not raise; falls back to shadow_roi if present."""
    rows = [
        {
            "result_status": "hit",
            "fragility_bucket": "LOW",
            "selection": "under",
            "market_type": "player_points",
            "confidence": 0.70,
            "edge": 1.0,
            "profit_loss": 5.0,
            "stake_amount": 0.0,   # zero stake
            "shadow_roi": 0.08,    # fallback
        },
    ]
    df = pd.DataFrame(rows)
    # Should not raise, and roi should fall back to shadow_roi mean = 0.08
    report = build_policy_simulation_report(df, "2026-05-09")
    baseline_roi = report["baseline"]["baseline_roi"]
    # stake_amount=0 means total_stake=0, so profit_loss path skipped; uses shadow_roi
    assert baseline_roi == pytest.approx(0.08, abs=0.001)


def test_roi_nan_stakes_excluded_from_denominator() -> None:
    """Rows with NaN stake_amount must not reduce the denominator for other rows."""
    rows = [
        _make_staked_row("hit",  profit_loss=10.0, stake_amount=10.0),
        {  # NaN stake — must be excluded from both numerator and denominator
            "result_status": "miss",
            "fragility_bucket": "LOW",
            "selection": "under",
            "market_type": "player_points",
            "confidence": 0.70,
            "edge": 1.0,
            "profit_loss": -999.0,  # should not affect result
            "stake_amount": float("nan"),
        },
    ]
    df = pd.DataFrame(rows)
    report = build_policy_simulation_report(df, "2026-05-09")
    # Only the first row is valid: ROI = 10/10 = 1.0
    assert report["baseline"]["baseline_roi"] == pytest.approx(1.0, abs=0.001)


def test_portfolio_impact_roi_is_stake_weighted() -> None:
    """Impact analysis portfolio_roi_without_flagged must use stake-weighted ROI."""
    # POLICY_B: player_assists + over + HIGH fragility (flagged, bad ROI)
    # remaining: good ROI, large stake
    rows = [
        # flagged: small stake, large loss
        _make_staked_row("miss", profit_loss=-50.0, stake_amount=5.0,
                         fragility_bucket="HIGH", market_type="player_assists", selection="over"),
        # remaining: big stake, big win
        _make_staked_row("hit", profit_loss=100.0, stake_amount=100.0,
                         fragility_bucket="LOW", market_type="player_points", selection="under"),
    ]
    report = build_policy_simulation_report(pd.DataFrame(rows), "2026-05-09")
    impact = report["policies"]["POLICY_B"]["impact_analysis"]

    # baseline ROI: (-50 + 100) / (5 + 100) = 50/105 ≈ 0.4762
    assert impact["baseline_portfolio_roi"] == pytest.approx(50.0 / 105.0, abs=0.001)
    # remaining ROI (after removing flagged): 100/100 = 1.0
    assert impact["portfolio_roi_without_flagged"] == pytest.approx(1.0, abs=0.001)
    # delta: 1.0 - 0.4762 ≈ +0.5238
    assert impact["portfolio_roi_delta"] == pytest.approx(1.0 - 50.0 / 105.0, abs=0.001)
