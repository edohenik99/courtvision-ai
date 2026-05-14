from __future__ import annotations

import json
import warnings
from pathlib import Path

import pandas as pd

from courtvision.reporting.low_line_over_minutes_guard_missed_winner_attribution import (
    build_low_line_over_minutes_guard_missed_winner_attribution,
    select_readiness_verdict,
    write_low_line_over_minutes_guard_missed_winner_attribution,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs


def _row(
    player_id: int,
    *,
    result_status: str,
    line: float = 12.5,
    actual_value: float = 10.0,
    edge: float = 1.0,
    confidence: float = 0.7,
    quality_score: float = 55.0,
    minutes_basis: float = 27.0,
    projected_minutes: float = 26.0,
    context_pick_alignment: str = "neutral",
    context_caution_level: str = "medium",
    team: str = "BOS",
    opponent: str = "LAL",
) -> dict:
    return {
        "prediction_date": "2026-05-13",
        "player_id": player_id,
        "player_name": f"Player {player_id}",
        "team_abbr": team,
        "opponent": opponent,
        "market_type": "player_points",
        "selection": "over",
        "line": line,
        "actual_value": actual_value,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "selection_score": quality_score,
        "minutes_basis": minutes_basis,
        "projected_minutes": projected_minutes,
        "usage_rate": 22.0,
        "model_projection": line + edge,
        "odds": -110,
        "context_pick_alignment": context_pick_alignment,
        "context_caution_level": context_caution_level,
        "defense_context_signal": "favorable" if result_status == "hit" else "negative",
        "pace_context_signal": "fast" if result_status == "hit" else "slow",
        "playoff_context_signal": "neutral",
        "result_status": result_status,
        "minutes_guard_review_bucket": "weak_minutes_basis",
    }


def _signal_rows() -> list[dict]:
    rows: list[dict] = []
    next_id = 1000
    for _ in range(12):
        rows.append(
            _row(
                next_id,
                result_status="hit",
                line=8.5,
                actual_value=13.0,
                edge=3.5,
                confidence=0.82,
                quality_score=70.0,
                minutes_basis=27.0,
                projected_minutes=27.0,
                context_pick_alignment="aligned",
                context_caution_level="low",
                team="BOS",
                opponent="ATL",
            )
        )
        next_id += 1
    for _ in range(20):
        rows.append(
            _row(
                next_id,
                result_status="miss",
                line=13.5,
                actual_value=10.0,
                edge=0.8,
                confidence=0.61,
                quality_score=50.0,
                minutes_basis=25.5,
                projected_minutes=24.0,
                context_pick_alignment="conflicted",
                context_caution_level="high",
                team="NYK",
                opponent="MIA",
            )
        )
        next_id += 1
    return rows


def _flat_rows() -> list[dict]:
    rows: list[dict] = []
    next_id = 2000
    for _ in range(15):
        row = _row(next_id, result_status="hit")
        row["defense_context_signal"] = "neutral"
        row["pace_context_signal"] = "neutral"
        rows.append(row)
        next_id += 1
    for _ in range(15):
        row = _row(next_id, result_status="miss")
        row["defense_context_signal"] = "neutral"
        row["pace_context_signal"] = "neutral"
        rows.append(row)
        next_id += 1
    return rows


def _actual_value_only_rows() -> list[dict]:
    rows: list[dict] = []
    next_id = 4000
    for _ in range(15):
        row = _row(next_id, result_status="hit", actual_value=14.0)
        row["defense_context_signal"] = "neutral"
        row["pace_context_signal"] = "neutral"
        rows.append(row)
        next_id += 1
    for _ in range(15):
        row = _row(next_id, result_status="miss", actual_value=8.0)
        row["defense_context_signal"] = "neutral"
        row["pace_context_signal"] = "neutral"
        rows.append(row)
        next_id += 1
    return rows


def _identity_only_rows() -> list[dict]:
    rows: list[dict] = []
    next_id = 5000
    for _ in range(15):
        row = _row(next_id, result_status="hit", team="BOS", opponent="ATL")
        row["player_name"] = "Identity Winner"
        row["defense_context_signal"] = "neutral"
        row["pace_context_signal"] = "neutral"
        rows.append(row)
        next_id += 1
    for _ in range(15):
        row = _row(next_id, result_status="miss", team="NYK", opponent="MIA")
        row["player_name"] = "Identity Loser"
        row["defense_context_signal"] = "neutral"
        row["pace_context_signal"] = "neutral"
        rows.append(row)
        next_id += 1
    return rows


def test_missed_winners_are_counted_correctly(tmp_path: Path) -> None:
    payload = build_low_line_over_minutes_guard_missed_winner_attribution(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(_signal_rows()),
        outcome_csv=pd.DataFrame(_signal_rows()),
        policy_simulation_csv=pd.DataFrame(),
    )

    assert payload["missed_winner_count"] == 12
    assert set(payload["attribution_df"]["attribution_group"]) == {"missed_winner", "saved_loser"}


def test_saved_losers_are_counted_correctly(tmp_path: Path) -> None:
    payload = build_low_line_over_minutes_guard_missed_winner_attribution(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(_signal_rows()),
        outcome_csv=pd.DataFrame(_signal_rows()),
        policy_simulation_csv=pd.DataFrame(),
    )

    assert payload["saved_loser_count"] == 20
    assert payload["net_saved_result_count"] == 8


def test_pending_rows_are_excluded(tmp_path: Path) -> None:
    rows = _signal_rows() + [
        _row(3001, result_status="pending"),
        _row(3002, result_status="open"),
    ]

    payload = build_low_line_over_minutes_guard_missed_winner_attribution(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(rows),
        outcome_csv=pd.DataFrame(rows),
        policy_simulation_csv=pd.DataFrame(),
    )

    assert payload["missed_winner_count"] == 12
    assert payload["saved_loser_count"] == 20
    assert payload["pending_rows_excluded"] == 2


def test_voids_are_excluded_from_winner_loser_comparison(tmp_path: Path) -> None:
    rows = _signal_rows() + [
        _row(3003, result_status="void"),
        _row(3004, result_status="void"),
        _row(3005, result_status="push"),
    ]

    payload = build_low_line_over_minutes_guard_missed_winner_attribution(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(rows),
        outcome_csv=pd.DataFrame(rows),
        policy_simulation_csv=pd.DataFrame(),
    )

    assert payload["missed_winner_count"] == 12
    assert payload["saved_loser_count"] == 20
    assert payload["voids_excluded_from_winner_loser"] == 2
    assert payload["pushes_excluded_from_winner_loser"] == 1


def test_average_metrics_are_calculated_correctly(tmp_path: Path) -> None:
    payload = build_low_line_over_minutes_guard_missed_winner_attribution(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(_signal_rows()),
        outcome_csv=pd.DataFrame(_signal_rows()),
        policy_simulation_csv=pd.DataFrame(),
    )

    winners = payload["metrics_by_group"]["missed_winners"]
    losers = payload["metrics_by_group"]["saved_losers"]
    assert winners["line"] == 8.5
    assert losers["line"] == 13.5
    assert winners["actual_value"] == 13.0
    assert losers["actual_value"] == 10.0
    assert payload["numeric_comparison"]["edge"]["winner_minus_loser"] == 2.7


def test_signal_comparison_and_candidate_rules_work(tmp_path: Path) -> None:
    payload = build_low_line_over_minutes_guard_missed_winner_attribution(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(_signal_rows()),
        outcome_csv=pd.DataFrame(_signal_rows()),
        policy_simulation_csv=pd.DataFrame(),
    )

    winner_signals = payload["strongest_winner_signals"]
    loser_signals = payload["strongest_loser_signals"]
    assert any(signal["signal"] == "missed_winners_lower_line" for signal in winner_signals)
    assert any(signal["signal"] == "saved_losers_higher_line" for signal in loser_signals)
    assert all(signal["field"] != "actual_value" for signal in winner_signals if "field" in signal)
    assert all(signal["field"] != "actual_value" for signal in loser_signals if "field" in signal)
    assert all({"winner_count", "loser_count", "total_count", "winner_share", "loser_share", "sample_status"} <= set(signal) for signal in winner_signals)
    assert payload["post_outcome_diagnostics"]["numeric_comparison"]["actual_value"]["winner_minus_loser"] == 3.0
    rules = payload["candidate_refinement_rules"]
    assert any("line_below" in rule["rule"] for rule in rules)
    assert any("edge_gte" in rule["rule"] for rule in rules)
    assert payload["readiness_verdict"] == "ATTRIBUTION_POLICY_REFINEMENT_CANDIDATE"


def test_actual_value_is_post_outcome_diagnostic_only(tmp_path: Path) -> None:
    payload = build_low_line_over_minutes_guard_missed_winner_attribution(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(_actual_value_only_rows()),
        outcome_csv=pd.DataFrame(_actual_value_only_rows()),
        policy_simulation_csv=pd.DataFrame(),
    )

    post = payload["post_outcome_diagnostics"]
    assert post["numeric_comparison"]["actual_value"]["winner_minus_loser"] == 6.0
    assert any(
        signal["signal"] == "missed_winners_higher_actual_value"
        for signal in post["strongest_winner_diagnostics"]
    )
    assert payload["actionable_pre_pick_signals"]["top_generalized_winner_signal"] == "no_generalized_pre_pick_signal"
    assert payload["actionable_pre_pick_signals"]["top_generalized_loser_signal"] == "no_generalized_pre_pick_signal"
    assert payload["strongest_winner_signals"] == []
    assert payload["strongest_loser_signals"] == []


def test_actual_value_cannot_generate_rules_or_refinement_verdict(tmp_path: Path) -> None:
    payload = build_low_line_over_minutes_guard_missed_winner_attribution(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(_actual_value_only_rows()),
        outcome_csv=pd.DataFrame(_actual_value_only_rows()),
        policy_simulation_csv=pd.DataFrame(),
    )

    assert payload["missed_winner_count"] == 15
    assert payload["saved_loser_count"] == 15
    assert payload["candidate_refinement_rules"] == []
    assert payload["candidate_refinement_rule_count"] == 0
    assert payload["readiness_verdict"] == "ATTRIBUTION_MIXED_NO_CLEAR_RULE"


def test_player_identity_signal_is_identity_diagnostic_only(tmp_path: Path) -> None:
    payload = build_low_line_over_minutes_guard_missed_winner_attribution(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(_identity_only_rows()),
        outcome_csv=pd.DataFrame(_identity_only_rows()),
        policy_simulation_csv=pd.DataFrame(),
    )

    identity = payload["identity_diagnostics"]
    assert any(
        signal["signal"] == "player_name:Identity Winner_leans_missed_winner"
        for signal in identity["strongest_winner_signals"]
    )
    top_identity = identity["strongest_winner_signals"][0]
    assert top_identity["winner_count"] == 15
    assert top_identity["loser_count"] == 0
    assert top_identity["total_count"] == 15
    assert top_identity["sample_status"] == "supported"
    assert payload["strongest_winner_signals"] == []
    assert payload["strongest_loser_signals"] == []


def test_identity_only_signal_does_not_create_rule_or_refinement_verdict(tmp_path: Path) -> None:
    payload = build_low_line_over_minutes_guard_missed_winner_attribution(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(_identity_only_rows()),
        outcome_csv=pd.DataFrame(_identity_only_rows()),
        policy_simulation_csv=pd.DataFrame(),
    )

    assert payload["candidate_refinement_rules"] == []
    assert payload["candidate_refinement_rule_count"] == 0
    assert payload["readiness_verdict"] == "ATTRIBUTION_MIXED_NO_CLEAR_RULE"
    assert payload["actionable_pre_pick_signals"]["top_generalized_winner_signal"] == "no_generalized_pre_pick_signal"
    assert payload["identity_diagnostics"]["top_identity_winner_signal"] != "no_identity_signal"


def test_insufficient_sample_verdict() -> None:
    assert (
        select_readiness_verdict(
            missed_winner_count=10,
            saved_loser_count=19,
            strongest_winner_signals=[],
            strongest_loser_signals=[],
            candidate_refinement_rules=[],
        )
        == "INSUFFICIENT_SAMPLE"
    )


def test_mixed_no_clear_rule_verdict(tmp_path: Path) -> None:
    payload = build_low_line_over_minutes_guard_missed_winner_attribution(
        "2026-05-13",
        runtime_root=tmp_path / "runtime",
        market_shadow_history=pd.DataFrame(_flat_rows()),
        outcome_csv=pd.DataFrame(_flat_rows()),
        policy_simulation_csv=pd.DataFrame(),
    )

    assert payload["missed_winner_count"] == 15
    assert payload["saved_loser_count"] == 15
    assert payload["strongest_winner_signals"] == []
    assert payload["strongest_loser_signals"] == []
    assert payload["candidate_refinement_rules"] == []
    assert payload["readiness_verdict"] == "ATTRIBUTION_MIXED_NO_CLEAR_RULE"


def test_writer_outputs_artifacts_without_mutating_history(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    history_root.mkdir(parents=True)
    history_path = history_root / "market_shadow_history.csv"
    pd.DataFrame(_signal_rows()).to_csv(history_path, index=False)
    before_history = history_path.read_bytes()

    json_path, txt_path, csv_path, payload = write_low_line_over_minutes_guard_missed_winner_attribution(
        "2026-05-13",
        runtime_root=runtime_root,
        market_shadow_history=history_path,
        outcome_csv=pd.DataFrame(_signal_rows()),
        policy_simulation_csv=pd.DataFrame(),
    )

    assert history_path.read_bytes() == before_history
    assert json_path.exists()
    assert txt_path.exists()
    assert csv_path.exists()
    assert payload["history_mutated"] is False
    assert payload["live_picks_suppressed"] is False
    assert "LOW-LINE OVER MINUTES GUARD MISSED WINNER ATTRIBUTION" in txt_path.read_text(encoding="utf-8")
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["readiness_verdict"] == "ATTRIBUTION_POLICY_REFINEMENT_CANDIDATE"


def test_quality_summary_section_renders_and_history_is_not_mutated(tmp_path: Path) -> None:
    prediction_date = "2026-05-13"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    research = runtime_root / "research"
    diagnostics = runtime_root / "diagnostics"
    model = tmp_path / "model"
    for directory in (operator, research, diagnostics, history_root, model):
        directory.mkdir(parents=True, exist_ok=True)

    rows = _signal_rows()
    first_row = rows[0] | {"is_live_market": True}
    pd.DataFrame([first_row]).to_csv(operator / f"elite_board_{prediction_date}.csv", index=False)
    pd.DataFrame([first_row]).to_csv(operator / f"full_market_board_{prediction_date}.csv", index=False)
    pd.DataFrame([first_row]).to_csv(research / f"player_predictions_{prediction_date}.csv", index=False)
    pd.DataFrame(rows).to_csv(history_root / "market_shadow_history.csv", index=False)
    pd.DataFrame(rows).to_csv(history_root / "pick_history.csv", index=False)
    pd.DataFrame([]).to_csv(history_root / "paper_kelly_history.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{prediction_date}.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"sgp_board_{prediction_date}.csv", index=False)
    pd.DataFrame([{"player_id": 1000, "player_name": "Player 1000", "team_abbr": "BOS", "min_avg": 27}]).to_csv(
        model / "player_baselines.csv",
        index=False,
    )
    (research / f"model_metrics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (diagnostics / f"board_diagnostics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (operator / f"elite_pipeline_audit_summary_{prediction_date}.json").write_text("{}", encoding="utf-8")

    shadow_before = (history_root / "market_shadow_history.csv").read_bytes()
    pick_before = (history_root / "pick_history.csv").read_bytes()
    paper_before = (history_root / "paper_kelly_history.csv").read_bytes()
    text_path, json_path, payload = write_quality_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        history_root=history_root,
    )

    assert (history_root / "market_shadow_history.csv").read_bytes() == shadow_before
    assert (history_root / "pick_history.csv").read_bytes() == pick_before
    assert (history_root / "paper_kelly_history.csv").read_bytes() == paper_before
    assert "low_line_over_minutes_guard_missed_winner_attribution" in payload
    attribution = payload["low_line_over_minutes_guard_missed_winner_attribution"]
    assert attribution["note"] == "review_only_no_prediction_grading_kelly_history_or_suppression_change"
    assert attribution["missed_winner_count"] == 12
    assert attribution["saved_loser_count"] == 20
    assert attribution["candidate_refinement_rule_count"] > 0
    assert attribution["top_generalized_winner_signal"] != "no_generalized_pre_pick_signal"
    assert attribution["top_identity_winner_signal"] != "no_identity_signal"
    assert Path(attribution["json_path"]).exists()
    assert Path(attribution["csv_path"]).exists()
    text = text_path.read_text(encoding="utf-8")
    assert "LOW-LINE OVER MINUTES GUARD MISSED WINNER ATTRIBUTION (Phase 15G -- REVIEW ONLY)" in text
    assert "top_generalized_winner_signal" in text
    assert "top_identity_winner_signal" in text
    assert "NOTE: REVIEW ONLY; no prediction/grading/Kelly/history changes and no picks suppressed." in text
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert "low_line_over_minutes_guard_missed_winner_attribution" in saved


def test_quality_summary_reports_no_actionable_signal_when_only_actual_value_differs(tmp_path: Path) -> None:
    prediction_date = "2026-05-13"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    research = runtime_root / "research"
    diagnostics = runtime_root / "diagnostics"
    model = tmp_path / "model"
    for directory in (operator, research, diagnostics, history_root, model):
        directory.mkdir(parents=True, exist_ok=True)

    rows = _actual_value_only_rows()
    first_row = rows[0] | {"is_live_market": True}
    pd.DataFrame([first_row]).to_csv(operator / f"elite_board_{prediction_date}.csv", index=False)
    pd.DataFrame([first_row]).to_csv(operator / f"full_market_board_{prediction_date}.csv", index=False)
    pd.DataFrame([first_row]).to_csv(research / f"player_predictions_{prediction_date}.csv", index=False)
    pd.DataFrame(rows).to_csv(history_root / "market_shadow_history.csv", index=False)
    pd.DataFrame(rows).to_csv(history_root / "pick_history.csv", index=False)
    pd.DataFrame([]).to_csv(history_root / "paper_kelly_history.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{prediction_date}.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"sgp_board_{prediction_date}.csv", index=False)
    pd.DataFrame([{"player_id": 4000, "player_name": "Player 4000", "team_abbr": "BOS", "min_avg": 27}]).to_csv(
        model / "player_baselines.csv",
        index=False,
    )
    (research / f"model_metrics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (diagnostics / f"board_diagnostics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (operator / f"elite_pipeline_audit_summary_{prediction_date}.json").write_text("{}", encoding="utf-8")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RuntimeWarning)
        text_path, _json_path, payload = write_quality_summary_outputs(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            out_dir=tmp_path,
            history_root=history_root,
        )

    runtime_warnings = [warning for warning in caught if issubclass(warning.category, RuntimeWarning)]
    assert runtime_warnings == []

    attribution = payload["low_line_over_minutes_guard_missed_winner_attribution"]
    assert attribution["top_generalized_winner_signal"] == "no_generalized_pre_pick_signal"
    assert attribution["top_generalized_loser_signal"] == "no_generalized_pre_pick_signal"
    assert attribution["candidate_refinement_rule_count"] == 0
    assert attribution["readiness_verdict"] == "ATTRIBUTION_MIXED_NO_CLEAR_RULE"
    text = text_path.read_text(encoding="utf-8")
    assert "top_generalized_winner_signal: no_generalized_pre_pick_signal" in text
    assert "top_generalized_loser_signal : no_generalized_pre_pick_signal" in text


def test_quality_summary_renders_identity_and_generalized_separately_for_identity_only(tmp_path: Path) -> None:
    prediction_date = "2026-05-13"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    research = runtime_root / "research"
    diagnostics = runtime_root / "diagnostics"
    model = tmp_path / "model"
    for directory in (operator, research, diagnostics, history_root, model):
        directory.mkdir(parents=True, exist_ok=True)

    rows = _identity_only_rows()
    first_row = rows[0] | {"is_live_market": True}
    pd.DataFrame([first_row]).to_csv(operator / f"elite_board_{prediction_date}.csv", index=False)
    pd.DataFrame([first_row]).to_csv(operator / f"full_market_board_{prediction_date}.csv", index=False)
    pd.DataFrame([first_row]).to_csv(research / f"player_predictions_{prediction_date}.csv", index=False)
    pd.DataFrame(rows).to_csv(history_root / "market_shadow_history.csv", index=False)
    pd.DataFrame(rows).to_csv(history_root / "pick_history.csv", index=False)
    pd.DataFrame([]).to_csv(history_root / "paper_kelly_history.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"kelly_stakes_{prediction_date}.csv", index=False)
    pd.DataFrame([]).to_csv(operator / f"sgp_board_{prediction_date}.csv", index=False)
    pd.DataFrame([{"player_id": 5000, "player_name": "Identity Winner", "team_abbr": "BOS", "min_avg": 27}]).to_csv(
        model / "player_baselines.csv",
        index=False,
    )
    (research / f"model_metrics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (diagnostics / f"board_diagnostics_{prediction_date}.json").write_text("{}", encoding="utf-8")
    (operator / f"elite_pipeline_audit_summary_{prediction_date}.json").write_text("{}", encoding="utf-8")

    text_path, _json_path, payload = write_quality_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        history_root=history_root,
    )

    attribution = payload["low_line_over_minutes_guard_missed_winner_attribution"]
    assert attribution["top_generalized_winner_signal"] == "no_generalized_pre_pick_signal"
    assert attribution["top_identity_winner_signal"] != "no_identity_signal"
    assert attribution["candidate_refinement_rule_count"] == 0
    assert attribution["readiness_verdict"] == "ATTRIBUTION_MIXED_NO_CLEAR_RULE"
    text = text_path.read_text(encoding="utf-8")
    assert "top_generalized_winner_signal: no_generalized_pre_pick_signal" in text
    assert "top_identity_winner_signal   :" in text
