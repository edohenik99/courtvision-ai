"""Focused NBA-01 supplemental checks for the three documented static gaps."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import socket
import tempfile

import pytest

DATE = "2026-05-06"
REASON = "economic_probability_provenance_unqualified"
ACTIONABLE = {"OK_TO_CONSIDER", "BET_NOW", "BET", "PLAY"}


def _modules():
    # Import the current consumers directly; no dependency on an absent test file.
    import pandas as pd
    from courtvision.reporting import quality_summary
    from scripts import run_kelly_stakes, write_daily_summary

    return pd, run_kelly_stakes, quality_summary, write_daily_summary


def _report_inputs(tmp_path, rows):
    pd, _, quality, daily = _modules()
    runtime = tmp_path / "reports" / "runtime"
    operator = runtime / "operator"
    operator.mkdir(parents=True, exist_ok=True)
    columns = ["prediction_date", "player_name", "market_type", "selection", "line"]
    frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=columns)
    # A nonempty board keeps quarantined rows visible to both report consumers.
    for stem in ("elite_board", "full_market_board", "kelly_stakes"):
        frame.to_csv(operator / f"{stem}_{DATE}.csv", index=False)
    pd.DataFrame(columns=["prediction_date"]).to_csv(
        operator / f"sgp_board_{DATE}.csv", index=False
    )
    text, payload = quality.build_quality_summary(
        prediction_date=DATE,
        runtime_root=runtime,
        out_dir=tmp_path / "reports",
        generated_at=f"{DATE}T00:00:00+00:00",
    )
    output, metadata = daily.write_daily_summary_outputs(
        prediction_date=DATE,
        runtime_root=runtime,
        history_root=tmp_path / "reports" / "history",
    )
    return text, payload, output.read_text(encoding="utf-8"), metadata


@pytest.fixture(autouse=True)
def boundary(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.exit("NBA01_UNEXPECTED_NETWORK_ATTEMPT", returncode=97)

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("COURTVISION_PLAYER_POINTS_RECALIBRATION", "off")

    assert os.environ["TEMP"] == os.environ["TMP"] == tempfile.gettempdir()
    assert Path.cwd().resolve() == Path(
        os.environ["COURTVISION_ISOLATED_TEST_ROOT"]
    ).resolve()


def test_legacy_flags_numeric_ev_are_not_admitted(tmp_path):
    rows = [{
        "prediction_date": DATE,
        "player_name": "Legacy",
        "market_type": "player_points",
        "selection": "under",
        "line": 14.5,
        "eligible": True,
        "kelly_eligible": True,
        "stake_amount": 20.0,
        "expected_value": 4.27,
        "recommended_action": "OK_TO_CONSIDER",
    }]

    _, payload, dtext, meta = _report_inputs(tmp_path, rows)
    ev = payload["kelly_safety_summary"]

    print("NBA01_SUPP_LEGACY " + json.dumps({
        "ev": ev,
        "daily_ev": meta.get("expected_ev"),
        "daily_money_lines": [
            x for x in dtext.splitlines()
            if "EV=" in x or x.startswith("Expected EV:")
        ],
    }), flush=True)

    assert ev["total_expected_value"] is None
    assert ev.get("expected_value_available_count", 0) == 0
    assert meta["expected_ev"] is None
    assert "$4.27" not in dtext
    assert "recommended_action=OK_TO_CONSIDER" not in dtext
    _assert_report_quarantined(payload, dtext, meta, count=1)


def test_contradictory_quarantine_cannot_render_money_or_action(tmp_path):
    rows = [{
        "prediction_date": DATE,
        "player_name": "Contradictory",
        "market_type": "player_points",
        "selection": "under",
        "line": 14.5,
        "eligible": True,
        "kelly_eligible": False,
        "stake_amount": 20.0,
        "expected_value": 4.27,
        "economic_ineligibility_reason": REASON,
        "recommended_action": "",
    }]

    _, payload, dtext, meta = _report_inputs(tmp_path, rows)

    assert payload["kelly_safety_summary"]["total_expected_value"] is None
    assert meta["expected_ev"] is None
    assert "stake=$20.00" not in dtext
    assert "EV=$4.27" not in dtext
    assert "recommended_action=OK_TO_CONSIDER" not in dtext
    _assert_report_quarantined(payload, dtext, meta, count=1)


def test_preconstructed_export_cannot_keep_actionable_strings(tmp_path):
    mods = _modules()
    stakes = next(
        m for m in mods
        if hasattr(m, "_build_stake_row") and hasattr(m, "_write_stakes")
    )

    row = {
        "player_id": "1001",
        "player_name": "Preconstructed",
        "team_abbr": "NYK",
        "opponent": "BOS",
        "market_type": "player_points",
        "selection": "over",
        "line": "14.5",
        "odds": "150",
        "side_edge_pct": "0.20",
        "confidence": "0.8",
        "context_caution_level": "low",
        "context_pick_alignment": "aligned",
        "row_identity_valid": "True",
    }

    stake = stakes._build_stake_row(row, "side_edge_pct", 1000.0)
    research_fields = (
        "player_id", "player_name", "team_abbr", "opponent", "market_type",
        "selection", "line", "edge_pct", "side_edge_pct", "confidence",
        "row_identity_valid", "source_identity_conflict_details",
    )
    research = {key: getattr(stake, key) for key in research_fields}

    stake.eligible = True
    stake.stake_fraction = 0.20
    stake.stake_amount = 200.0
    stake.expected_value = 99.0
    stake.recommended_action = "BET_NOW"
    stake.operator_action = "BET_NOW"
    stake.manual_review_required = False
    stake.review_before_bet = False
    stake.review_policy_hold = False
    stake.stake_policy = "NORMAL"
    stake.review_status = "CLEAR"

    if hasattr(stake, "economic_ineligibility_reason"):
        stake.economic_ineligibility_reason = ""

    output = tmp_path / "operator" / f"kelly_stakes_{DATE}.csv"

    stakes._write_stakes(
        output,
        [stake],
        1000.0,
        DATE,
    )

    with output.open(newline="", encoding="utf-8") as handle:
        saved = list(csv.DictReader(handle))[0]

    print(
        "NBA01_SUPP_PRECONSTRUCTED "
        + json.dumps(saved, sort_keys=True),
        flush=True,
    )

    assert saved["eligible"].lower() == "false"
    assert saved["kelly_eligible"].lower() == "false"
    assert float(saved["stake_amount"]) == 0.0
    assert saved["expected_value"] == ""
    assert saved.get("recommended_action", "").upper() not in ACTIONABLE
    assert saved.get("operator_action", "").upper() not in ACTIONABLE

    assert float(saved["stake_fraction"]) == 0.0
    assert saved["economic_ineligibility_reason"] == REASON
    for key in ("eligible", "kelly_eligible"):
        assert saved[key] == "False"
    for key in ("recommended_action", "operator_action"):
        assert saved[key] == "DO_NOT_BET_UNTIL_REVIEWED"
    for key in ("manual_review_required", "review_before_bet", "review_policy_hold"):
        assert saved[key] == "True"
    assert saved["stake_policy"] == "HOLD"
    assert saved["review_status"] == "REVIEW_REQUIRED"
    assert {key: getattr(stake, key) for key in research_fields} == research
    for key in ("edge_pct", "side_edge_pct", "confidence"):
        assert float(saved[key]) == research[key]
    for key in ("player_id", "player_name", "market_type", "selection"):
        assert saved[key] == research[key]
    _, payload, dtext, meta = _report_inputs(tmp_path, [saved])
    _assert_report_quarantined(payload, dtext, meta, count=1)
    assert json.loads(json.dumps(payload))["kelly_safety_summary"]["total_expected_value"] is None


def _assert_report_quarantined(payload, dtext, meta, *, count):
    safety = payload["kelly_safety_summary"]
    assert safety["total_rows"] == count
    assert safety["kelly_eligible_count"] == 0
    assert safety["skipped_count"] == count
    assert safety["total_stake"] == 0.0
    assert safety["total_expected_value"] is None
    assert safety["expected_value_available_count"] == 0
    assert safety["expected_value_unavailable_count"] == count
    assert meta["kelly_eligible_count"] == 0
    assert meta["total_exposure"] == 0.0
    assert meta["expected_ev"] is None
    assert "Total exposure: $0.00" in dtext
    assert "Expected EV: n/a" in dtext
    exposure = payload["risk_exposure_summary"]
    for key in ("max_player_exposure", "max_team_exposure", "max_game_exposure"):
        assert exposure[key] == 0.0
    for key in ("by_player", "by_team", "by_game"):
        assert all(value == 0.0 for value in exposure[key].values())
    money_lines = [line for line in dtext.splitlines() if " stake=" in line and " EV=" in line]
    assert len(money_lines) == count
    for line in money_lines:
        assert "stake=$0.00 EV=n/a" in line
        assert "recommended_action=DO_NOT_BET_UNTIL_REVIEWED" in line
        assert REASON in line
        for action in ACTIONABLE:
            assert f"recommended_action={action}" not in line
    for key in ("manual_review_required_count", "review_before_bet_count", "review_policy_hold_count"):
        assert safety[key] == count
    assert f"manual_review_required_count: {count}" in dtext
    assert f"review_before_bet_count: {count}" in dtext
    assert f"hold_policy_count: {count}" in dtext


@pytest.mark.parametrize("expected_value", [0.0, 4.27, None])
@pytest.mark.parametrize("extra", [
    {},
    {"kelly_eligible": False, "economic_ineligibility_reason": REASON},
    {
        "probability_schema_version": "nba-player-points-probability-v1",
        "model_approval_status": "production_approved",
        "model_over_probability": 0.6,
        "model_under_probability": 0.4,
    },
])
def test_direct_reporting_boundaries_require_economic_provenance(expected_value, extra):
    # The separate research probability contract is not an approved monetary
    # path. In this legacy artifact even a stored zero must remain unavailable;
    # do not fabricate a qualified-zero fixture from approval-looking fields.
    pd, _, quality, daily = _modules()
    row = {
        "player_id": "1001", "player_name": "Direct boundary", "team_abbr": "NYK",
        "game_id": "2001", "market_type": "player_points", "selection": "under",
        "projection": 11.4, "confidence": 0.8, "edge_pct": 0.2, "side_edge_pct": 0.2,
        "eligible": True, "kelly_eligible": True, "stake_fraction": 0.02,
        "stake_amount": 20.0, "recommended_bet": 20.0, "expected_value": expected_value,
        "recommended_action": "CUSTOM_BET_ACTION", "operator_action": "BET_NOW",
        "diagnostic_metadata": {"source": "research"}, **extra,
    }
    frame = pd.DataFrame([row])
    original = frame.copy(deep=True)
    summary = quality._kelly_safety_summary(frame)
    ev = quality._financial_ev_summary(frame)
    exposure = quality._exposure_summary(frame, pd.DataFrame())
    counts = quality._kelly_counts_by_market(frame)
    line = daily._kelly_line(frame.iloc[0])
    assert summary["total_stake"] == 0.0
    assert summary["kelly_eligible_count"] == 0
    assert ev["total_expected_value"] is None
    assert ev["expected_value_available_count"] == 0
    assert ev["expected_value_reasons"] == {REASON: 1}
    assert exposure["by_player"] == {"Direct boundary": 0.0}
    assert exposure["by_team"] == {"NYK": 0.0}
    assert exposure["by_game"] == {"2001": 0.0}
    assert counts["player_points"]["kelly_eligible_count"] == 0
    assert "stake=$0.00 EV=n/a" in line
    assert "recommended_action=DO_NOT_BET_UNTIL_REVIEWED" in line
    assert "CUSTOM_BET_ACTION" not in line
    assert REASON in line
    pd.testing.assert_frame_equal(frame, original)
    sanitized = quality._quarantine_economic_reporting(frame)
    assert sanitized.loc[0, "recommended_bet"] == 0.0
    research_columns = [
        "player_id", "player_name", "team_abbr", "game_id", "market_type",
        "selection", "projection", "confidence", "edge_pct", "side_edge_pct",
        "diagnostic_metadata",
    ]
    pd.testing.assert_frame_equal(sanitized[research_columns], original[research_columns])


@pytest.mark.parametrize("rows", [
    [],
    [
        {"player_name": "Legacy", "market_type": "player_points", "eligible": True,
         "kelly_eligible": True, "stake_amount": 20.0, "expected_value": 4.27},
        {"player_name": "Quarantined", "market_type": "player_points", "eligible": True,
         "kelly_eligible": False, "stake_amount": 200.0, "expected_value": 99.0,
         "economic_ineligibility_reason": REASON, "recommended_action": "PLAY"},
        {"player_name": "Unqualified zero", "market_type": "player_points",
         "stake_amount": 50.0, "expected_value": 0.0},
    ],
])
def test_empty_and_mixed_unqualified_reporting_cohorts(tmp_path, rows):
    # No supported qualified monetary path exists; this mixed legacy cohort
    # must not acquire one merely because some rows contain finite or zero EV.
    _, payload, dtext, meta = _report_inputs(tmp_path, rows)
    _assert_report_quarantined(payload, dtext, meta, count=len(rows))
    reasons = payload["kelly_safety_summary"]["expected_value_reasons"]
    assert reasons == ({REASON: len(rows)} if rows else {"financial_ev_empty_cohort": 1})
