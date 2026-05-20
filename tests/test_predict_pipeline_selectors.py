from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from courtvision.pipeline.predict_pipeline import PredictionConfig, PredictionPipeline
from courtvision.selection.pipeline_selectors import select_top_per_market


class _ConfigProxy:
    def __init__(self, base: PredictionConfig, overrides: dict[str, Any] | None = None) -> None:
        self._base = base
        for key, value in (overrides or {}).items():
            setattr(self, key, value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _future_game_datetime() -> str:
    return (datetime.now() + timedelta(hours=4)).isoformat()


def _fresh_odds_timestamp() -> str:
    return (datetime.now() - timedelta(minutes=5)).isoformat()


def _candidate(label: str, **overrides: Any) -> dict[str, Any]:
    line = float(overrides.pop("line", 20.5))
    selection = str(overrides.pop("selection", "under"))
    edge = float(overrides.pop("edge", -2.0 if selection == "under" else 2.0))
    row: dict[str, Any] = {
        "prediction_date": "2024-01-15",
        "player_name": label,
        "entity_name": label,
        "player_id": label,
        "team": "AAA",
        "team_abbr": "AAA",
        "game_id": "selector-game",
        "market_type": "player_points",
        "raw_prop_type": "points",
        "raw_market_type": "over_under",
        "selection": selection,
        "line": line,
        "sportsbook_line": line,
        "model_projection": line + edge,
        "projection": line + edge,
        "minutes_avg": 34.0,
        "minutes_recent": 34.0,
        "odds": -110,
        "edge": edge,
        "edge_pct": -0.08 if selection == "under" else 0.08,
        "side_edge": abs(edge),
        "side_edge_pct": 0.08,
        "confidence": 0.82,
        "quality_score": 82.0,
        "selection_score": 82.0,
        "is_elite": True,
        "is_live_market": True,
        "synthetic_line": False,
        "line_source": "live_market",
        "qualification_reason": "live_market_qualified",
        "source_lane": "live_market_candidate",
        "selection_rejection_reason": "",
        "stake_fraction": 0.01,
        "recommended_bet": 10.0,
        "game_status": "scheduled",
        "game_datetime": _future_game_datetime(),
        "game_date": _future_game_datetime(),
        "odds_updated_at": _fresh_odds_timestamp(),
    }
    row.update(overrides)
    return row


def _run_pipeline_with_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    candidates: list[dict[str, Any]],
    *,
    config_overrides: dict[str, Any] | None = None,
    logger_name: str = "test_predict_pipeline_selectors",
):
    base_config = PredictionConfig(
        prediction_date="2024-01-15",
        out_dir=str(tmp_path / "outputs"),
    )
    config = _ConfigProxy(base_config, config_overrides)
    pipeline = PredictionPipeline(config, logger=logging.getLogger(logger_name))

    def fake_build_candidate_universe(**_: Any):
        return candidates, [], {"conflict_count": 0, "counts_by_reason": {}}

    monkeypatch.setattr(
        pipeline,
        "_build_candidate_universe",
        fake_build_candidate_universe,
    )
    games = pd.DataFrame(
        [
            {
                "game_id": "selector-game",
                "home_team_abbr": "AAA",
                "visitor_team_abbr": "BBB",
                "game_status": "scheduled",
                "datetime": _future_game_datetime(),
            }
        ]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": "selector-game",
                "player_name": row["player_name"],
                "market_type": row["market_type"],
                "updated_at": row["odds_updated_at"],
                "is_live": row["is_live_market"],
            }
            for row in candidates
        ]
    )
    baselines = pd.DataFrame(
        [
            {
                "player_name": row["player_name"],
                "team_abbr": row["team_abbr"],
                "player_id": row["player_id"],
            }
            for row in candidates
        ]
    )
    return pipeline.run(games, odds, baselines), config


def _board_trace_from_caplog(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    for record in reversed(caplog.records):
        if record.msg == "board_selection_trace %s" and record.args:
            trace = record.args if isinstance(record.args, dict) else record.args[0]
            assert isinstance(trace, dict)
            return trace
    raise AssertionError("board_selection_trace log was not captured")


def _elite_telemetry_rows(config: Any) -> list[dict[str, Any]]:
    path = (
        Path(config.out_dir)
        / "runtime"
        / "operator"
        / "elite_pipeline_audit_summary_2024-01-15.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["rows"]
    assert isinstance(rows, list)
    return rows


def test_extracted_full_market_selector_preserves_nested_selection_behavior() -> None:
    candidates = pd.DataFrame(
        [
            {
                "player_name": "Points Low",
                "market_type": "player_points",
                "selection_score": 10.0,
                "quality_score": 99.0,
                "selection_rejection_reason": "stale",
            },
            {
                "player_name": "Points High",
                "market_type": "player_points",
                "selection_score": 30.0,
                "quality_score": 10.0,
                "selection_rejection_reason": "stale",
            },
            {
                "player_name": "Points Mid",
                "market_type": "player_points",
                "selection_score": 20.0,
                "quality_score": 20.0,
                "selection_rejection_reason": "stale",
            },
            {
                "player_name": "Rebound High",
                "market_type": "player_rebounds",
                "selection_score": 5.0,
                "quality_score": 5.0,
                "selection_rejection_reason": "stale",
            },
        ]
    )

    selected = select_top_per_market(candidates, per_market_limit=2)

    assert list(selected["player_name"]) == ["Points High", "Points Mid", "Rebound High"]
    assert list(selected.columns) == list(candidates.columns)
    assert selected["selection_rejection_reason"].tolist() == ["", "", ""]

    no_selection_score = candidates.drop(columns=["selection_score"])
    quality_selected = select_top_per_market(no_selection_score, per_market_limit=1)
    assert list(quality_selected["player_name"]) == ["Points Low", "Rebound High"]

    empty_candidates = candidates.iloc[0:0]
    assert select_top_per_market(empty_candidates, per_market_limit=2) is empty_candidates


def test_nested_full_market_selector_keeps_top_twenty_by_market_selection_score(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    points_rows = [
        _candidate(
            f"Points {score:02d}",
            player_id=f"points-{score}",
            line=10.0 + score,
            selection_score=float(score),
            quality_score=60.0 + score,
        )
        for score in range(1, 23)
    ]
    rebound_rows = [
        _candidate(
            "Rebound Top",
            player_id="reb-top",
            market_type="player_rebounds",
            raw_prop_type="rebounds",
            line=9.5,
            selection_score=50.0,
        ),
        _candidate(
            "Rebound Middle",
            player_id="reb-mid",
            market_type="player_rebounds",
            raw_prop_type="rebounds",
            line=8.5,
            selection_score=30.0,
        ),
        _candidate(
            "Rebound Low",
            player_id="reb-low",
            market_type="player_rebounds",
            raw_prop_type="rebounds",
            line=7.5,
            selection_score=10.0,
        ),
    ]

    result, _ = _run_pipeline_with_candidates(
        monkeypatch,
        tmp_path,
        points_rows + rebound_rows,
    )

    full_market = result.full_market_props
    points_scores = full_market.loc[
        full_market["market_type"].eq("player_points"),
        "selection_score",
    ].tolist()
    rebound_names = full_market.loc[
        full_market["market_type"].eq("player_rebounds"),
        "player_name",
    ].tolist()

    assert points_scores == [float(score) for score in range(22, 2, -1)]
    assert set(full_market["player_name"]).isdisjoint({"Points 01", "Points 02"})
    assert rebound_names == ["Rebound Top", "Rebound Middle", "Rebound Low"]
    assert len(full_market) == 23


def test_nested_elite_selector_default_points_only_records_current_rejection_reasons(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    candidates = [
        _candidate(
            "Rebound Market Filtered",
            player_id="market-filtered",
            market_type="player_rebounds",
            raw_prop_type="rebounds",
            line=9.5,
            selection_score=95.0,
        ),
        _candidate(
            "Direction Rejected",
            player_id="direction-rejected",
            selection="over",
            edge=-1.0,
            edge_pct=-0.05,
            side_edge=1.0,
            side_edge_pct=0.05,
            selection_score=90.0,
        ),
        _candidate(
            "Quality Rejected",
            player_id="quality-rejected",
            is_elite=False,
            quality_score=40.0,
            confidence=0.50,
            selection_score=85.0,
        ),
    ]

    result, config = _run_pipeline_with_candidates(monkeypatch, tmp_path, candidates)

    assert result.elite_props.empty
    assert result.selected_props.empty
    assert list(result.full_market_props["player_name"]) == [
        "Rebound Market Filtered",
        "Direction Rejected",
        "Quality Rejected",
    ]
    telemetry_reasons = {
        row["rejection_reason"]: int(row["count"])
        for row in _elite_telemetry_rows(config)
    }
    assert telemetry_reasons["market_filtered_by_elite_policy"] == 1
    assert telemetry_reasons["reject_negative_edge_direction"] == 1
    assert telemetry_reasons["reject_quality_confidence_threshold"] == 1


def test_nested_elite_selector_enforces_exposure_caps_before_final_elite_board(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidates = [
        _candidate("Cap Rank 1", player_id="cap-1", team="AAA", team_abbr="AAA", game_id=9001, selection_score=100.0),
        _candidate("Cap Rank 2", player_id="cap-2", team="BBB", team_abbr="BBB", game_id=9001, selection_score=90.0),
        _candidate("Cap Rank 3", player_id="cap-3", team="CCC", team_abbr="CCC", game_id=9001, selection_score=80.0),
        _candidate("Cap Rank 4", player_id="cap-4", team="DDD", team_abbr="DDD", game_id=9001, selection_score=70.0),
    ]

    with caplog.at_level(logging.INFO, logger="test_predict_pipeline_selectors"):
        result, _ = _run_pipeline_with_candidates(
            monkeypatch,
            tmp_path,
            candidates,
            config_overrides={"elite_team_cap": 10, "elite_game_cap": 2, "elite_size": 10},
        )

    trace = _board_trace_from_caplog(caplog)
    assert list(result.elite_props["player_name"]) == ["Cap Rank 1", "Cap Rank 2"]
    assert result.summary["elite_max_game_exposure"] == 2
    assert trace["elite"]["skipped_by_game_cap"] == 2
    assert trace["elite"]["candidate_count_after_concentration_caps"] == 2
    assert result.elite_props["selection_rejection_reason"].fillna("").eq("").all()


def test_nested_selectors_use_post_live_identity_and_duplicate_gate_pool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    candidates = [
        _candidate("Valid Live", player_id="valid", line=20.5, selection_score=20.0),
        _candidate(
            "Duplicate Lower",
            player_id="dupe",
            line=21.5,
            selection_score=10.0,
            quality_score=95.0,
        ),
        _candidate(
            "Duplicate Higher",
            player_id="dupe",
            line=21.5,
            selection_score=50.0,
            quality_score=60.0,
        ),
        _candidate(
            "Non Live Highest",
            player_id="non-live",
            line=22.5,
            selection_score=999.0,
            is_live_market=False,
        ),
        _candidate(
            "Identity Quarantined Highest",
            player_id="identity-quarantine",
            line=23.5,
            selection_score=998.0,
            candidate_team_not_in_game=True,
        ),
    ]

    result, _ = _run_pipeline_with_candidates(monkeypatch, tmp_path, candidates)

    full_market_names = list(result.full_market_props["player_name"])
    elite_names = list(result.elite_props["player_name"])
    assert full_market_names == ["Duplicate Higher", "Valid Live"]
    assert elite_names == ["Duplicate Higher", "Valid Live"]
    assert "Non Live Highest" not in set(full_market_names + elite_names)
    assert "Identity Quarantined Highest" not in set(full_market_names + elite_names)
    assert "Duplicate Lower" not in set(full_market_names + elite_names)
    assert result.summary["identity_quarantine_count"] == 1
    assert result.summary["identity_quarantine_reason_counts"] == {
        "outside_team_identity": 1,
    }
    assert result.summary["duplicate_betting_identity_drop_count"] == 1
    assert result.summary["duplicate_betting_identity_drop_counts_by_market_type"] == {
        "player_points": 1,
    }
    expected_board_columns = {
        "player_name",
        "market_type",
        "selection",
        "line",
        "odds",
        "confidence",
        "quality_score",
        "selection_score",
        "stake_fraction",
        "recommended_bet",
        "is_live_market",
        "synthetic_line",
        "source_lane",
        "qualification_reason",
        "game_status",
        "game_datetime",
    }
    assert expected_board_columns.issubset(result.full_market_props.columns)
    assert expected_board_columns.issubset(result.elite_props.columns)
