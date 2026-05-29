from __future__ import annotations

from pathlib import Path

from courtvision.reporting.no_bet_funnel import (
    STATUS_NO_BET,
    STATUS_REVIEW_REQUIRED,
    build_no_bet_funnel_report,
    parse_operator_card,
    write_no_bet_funnel_report_outputs,
)


def _operator_card_text(
    prediction_date: str,
    *,
    final_decision: str,
    run_health: str,
    full_market: int,
    near_elite: int,
    incubator: int,
    elite: int,
    kelly_rows: int,
    kelly_eligible: int,
    high_caution_over: int,
    combo_under: int = 0,
    same_opponent: int = 0,
    unsupported_active: int = 0,
    top_reason: str = "elite_reject_context_high_caution_over",
) -> str:
    return f"""prediction_date: {prediction_date}
run_health: {run_health}
final_decision: {final_decision}
full market candidates count: {full_market}
near-elite review count: {near_elite}
incubator board count: {incubator}
elite picks count: {elite}
Kelly rows count: {kelly_rows}
Kelly eligible count: {kelly_eligible}
high caution OVER count: {high_caution_over}
combo UNDER watchlist count: {combo_under}
same-opponent warning count: {same_opponent}
unsupported active markets dropped: {unsupported_active}
top rejection reason: {top_reason} ({high_caution_over})
"""


def _write_operator_card(runtime_root: Path, prediction_date: str, text: str) -> None:
    path = runtime_root / "operator" / f"operator_card_{prediction_date}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_parse_operator_card_counts_and_status() -> None:
    text = _operator_card_text(
        "2026-05-28",
        final_decision="NO BET",
        run_health="NO_BET",
        full_market=58,
        near_elite=8,
        incubator=1,
        elite=0,
        kelly_rows=0,
        kelly_eligible=0,
        high_caution_over=45,
        combo_under=2,
        same_opponent=1,
        unsupported_active=4,
    )

    parsed = parse_operator_card(text)

    assert parsed["prediction_date"] == "2026-05-28"
    assert parsed["decision_bucket"] == STATUS_NO_BET
    assert parsed["full_market_count"] == 58
    assert parsed["near_elite_count"] == 8
    assert parsed["incubator_count"] == 1
    assert parsed["kelly_eligible_count"] == 0
    assert parsed["high_caution_over_count"] == 45
    assert parsed["combo_under_watchlist_count"] == 2
    assert parsed["same_opponent_warning_count"] == 1
    assert parsed["unsupported_active_market_count"] == 4
    assert parsed["top_rejection_reason"] == "elite_reject_context_high_caution_over"
    assert parsed["top_rejection_count"] == 45


def test_build_report_aggregates_no_bet_streak_and_high_caution_rate(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _write_operator_card(
        runtime_root,
        "2026-05-01",
        _operator_card_text(
            "2026-05-01",
            final_decision="REVIEW REQUIRED",
            run_health="REVIEW REQUIRED",
            full_market=10,
            near_elite=1,
            incubator=0,
            elite=1,
            kelly_rows=1,
            kelly_eligible=1,
            high_caution_over=2,
        ),
    )
    _write_operator_card(
        runtime_root,
        "2026-05-02",
        _operator_card_text(
            "2026-05-02",
            final_decision="NO BET",
            run_health="NO_BET",
            full_market=20,
            near_elite=3,
            incubator=1,
            elite=0,
            kelly_rows=0,
            kelly_eligible=0,
            high_caution_over=10,
        ),
    )
    _write_operator_card(
        runtime_root,
        "2026-05-03",
        _operator_card_text(
            "2026-05-03",
            final_decision="NO BET",
            run_health="NO_BET",
            full_market=30,
            near_elite=4,
            incubator=0,
            elite=0,
            kelly_rows=0,
            kelly_eligible=0,
            high_caution_over=15,
        ),
    )

    payload, slate_df = build_no_bet_funnel_report(
        prediction_date="2026-05-04",
        runtime_root=runtime_root,
        history_root=history_root,
        lookback=3,
    )

    assert payload["status_counts"][STATUS_NO_BET] == 2
    assert payload["status_counts"][STATUS_REVIEW_REQUIRED] == 1
    assert payload["no_bet_streak"]["current_no_bet_streak"] == 2
    assert payload["aggregate"]["total_full_market_candidates"] == 60
    assert payload["aggregate"]["total_high_caution_over_blocks"] == 27
    assert payload["aggregate"]["high_caution_over_block_rate"] == 0.45
    assert payload["aggregate"]["total_near_elite_rows"] == 8
    assert payload["aggregate"]["total_incubator_rows"] == 1
    assert payload["aggregate"]["total_elite_rows"] == 1
    assert payload["aggregate"]["total_kelly_eligible_rows"] == 1
    assert len(slate_df) == 3


def test_report_handles_missing_optional_artifacts(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _write_operator_card(
        runtime_root,
        "2026-05-10",
        _operator_card_text(
            "2026-05-10",
            final_decision="NO BET",
            run_health="NO_BET",
            full_market=7,
            near_elite=2,
            incubator=0,
            elite=0,
            kelly_rows=0,
            kelly_eligible=0,
            high_caution_over=4,
            combo_under=1,
        ),
    )

    payload, _slate_df = build_no_bet_funnel_report(
        prediction_date="2026-05-11",
        runtime_root=runtime_root,
        history_root=history_root,
        lookback=14,
    )

    assert payload["operator_card_count"] == 1
    assert payload["aggregate"]["total_full_market_candidates"] == 7
    assert payload["aggregate"]["total_high_caution_over_blocks"] == 4
    assert payload["aggregate"]["total_combo_under_watchlist_blocks"] == 1


def test_write_outputs_does_not_modify_pick_history(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    pick_history = history_root / "pick_history.csv"
    pick_history.parent.mkdir(parents=True, exist_ok=True)
    pick_history.write_text("prediction_date,result_status\n2026-05-01,hit\n", encoding="utf-8")
    original_pick_history = pick_history.read_text(encoding="utf-8")
    _write_operator_card(
        runtime_root,
        "2026-05-12",
        _operator_card_text(
            "2026-05-12",
            final_decision="NO BET",
            run_health="NO_BET",
            full_market=12,
            near_elite=1,
            incubator=0,
            elite=0,
            kelly_rows=0,
            kelly_eligible=0,
            high_caution_over=9,
        ),
    )

    text_path, json_path, csv_path, payload = write_no_bet_funnel_report_outputs(
        prediction_date="2026-05-13",
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert text_path.exists()
    assert json_path.exists()
    assert csv_path.exists()
    assert payload["read_only"] is True
    assert payload["betting_logic_changed"] is False
    assert pick_history.read_text(encoding="utf-8") == original_pick_history


def test_report_is_read_only_and_does_not_require_current_slate_rerun(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    _write_operator_card(
        runtime_root,
        "2026-05-20",
        _operator_card_text(
            "2026-05-20",
            final_decision="NO BET",
            run_health="NO_BET",
            full_market=22,
            near_elite=4,
            incubator=0,
            elite=0,
            kelly_rows=0,
            kelly_eligible=0,
            high_caution_over=16,
        ),
    )

    payload, _slate_df = build_no_bet_funnel_report(
        prediction_date="2026-05-29",
        runtime_root=runtime_root,
        history_root=history_root,
        lookback=14,
    )

    assert payload["read_only"] is True
    assert payload["betting_logic_changed"] is False
    assert payload["date_range"]["end"] == "2026-05-20"
    assert payload["operator_card_count"] == 1
