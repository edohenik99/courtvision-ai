from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from courtvision.config import EliteThresholds
from courtvision.pipeline.predict_pipeline import PredictionConfig, PredictionPipeline
from courtvision.reason_codes import REJECT_NEGATIVE_EDGE_DIRECTION
from courtvision.selection.pipeline_selectors import (
    elite_direction_rejection_reason,
    elite_market_policy_rejection_reason,
    resolve_elite_allowed_markets,
    select_top_per_market,
)


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
        "opponent": "BBB",
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


def _elite_telemetry_reason_counts(config: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in _elite_telemetry_rows(config):
        reason = str(row["rejection_reason"])
        counts[reason] = counts.get(reason, 0) + int(row["count"])
    return counts


def test_elite_policy_helpers_preserve_current_market_modes_and_reasons() -> None:
    assert resolve_elite_allowed_markets() == {"player_points"}
    assert resolve_elite_allowed_markets("points_only") == {"player_points"}
    assert resolve_elite_allowed_markets("player_props") == {
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_3pt_made",
        "player_steals",
        "player_blocks",
    }
    assert resolve_elite_allowed_markets("full") == {
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_3pt_made",
        "player_steals",
        "player_blocks",
        "moneyline",
        "team_total",
    }
    assert resolve_elite_allowed_markets(
        "full",
        elite_allowed_markets=("points", "rebounds"),
    ) == {"player_points", "player_rebounds"}

    allowed = resolve_elite_allowed_markets()
    assert elite_market_policy_rejection_reason("player_points", allowed) is None
    assert elite_market_policy_rejection_reason("points", allowed) is None
    assert (
        elite_market_policy_rejection_reason("player_rebounds", allowed)
        == "market_filtered_by_elite_policy"
    )


def test_elite_direction_helper_preserves_points_edge_reason() -> None:
    assert elite_direction_rejection_reason("player_points", "over", -0.01) == REJECT_NEGATIVE_EDGE_DIRECTION
    assert elite_direction_rejection_reason("player_points", "over", 0.0) == REJECT_NEGATIVE_EDGE_DIRECTION
    assert elite_direction_rejection_reason("player_points", "under", 0.01) == REJECT_NEGATIVE_EDGE_DIRECTION
    assert elite_direction_rejection_reason("player_points", "under", 0.0) == REJECT_NEGATIVE_EDGE_DIRECTION
    assert elite_direction_rejection_reason("player_points", "over", 0.01) is None
    assert elite_direction_rejection_reason("player_points", "under", -0.01) is None
    assert elite_direction_rejection_reason("player_rebounds", "over", -0.01) is None


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


def test_nested_elite_selector_default_points_only_admits_allowed_points_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidates = [
        _candidate("Points Allowed", player_id="points-allowed", selection_score=90.0),
        _candidate(
            "Rebound Policy Rejected",
            player_id="rebound-policy",
            market_type="player_rebounds",
            raw_prop_type="rebounds",
            line=9.5,
            selection_score=100.0,
        ),
        _candidate(
            "Assists Policy Rejected",
            player_id="assists-policy",
            market_type="player_assists",
            raw_prop_type="assists",
            line=7.5,
            selection_score=95.0,
        ),
    ]

    with caplog.at_level(logging.INFO, logger="test_predict_pipeline_selectors"):
        result, config = _run_pipeline_with_candidates(monkeypatch, tmp_path, candidates)

    trace = _board_trace_from_caplog(caplog)
    telemetry_reasons = _elite_telemetry_reason_counts(config)

    assert list(result.elite_props["player_name"]) == ["Points Allowed"]
    assert set(result.full_market_props["player_name"]) == {
        "Points Allowed",
        "Rebound Policy Rejected",
        "Assists Policy Rejected",
    }
    assert telemetry_reasons["passed_to_elite"] == 1
    assert telemetry_reasons["market_filtered_by_elite_policy"] == 2
    assert trace["elite"]["candidate_count_entering_elite_selection"] == 3
    assert trace["elite"]["candidate_count_after_elite_admission_filter"] == 1
    assert trace["elite"]["candidate_count_after_concentration_caps"] == 1
    assert trace["elite"]["candidate_count_after_backfill"] == 1
    assert trace["elite"]["selected_count"] == 1


def test_nested_elite_selector_rejection_reasons_and_no_bet_output_remain_stable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidates = [
        _candidate(
            "Market Rejected",
            player_id="market-rejected",
            market_type="player_rebounds",
            raw_prop_type="rebounds",
            line=9.5,
            selection_score=95.0,
        ),
        _candidate(
            "Negative Edge Rejected",
            player_id="negative-edge-rejected",
            selection="over",
            edge=-1.0,
            edge_pct=-0.05,
            side_edge=1.0,
            side_edge_pct=0.05,
            selection_score=90.0,
        ),
        _candidate(
            "Quality Confidence Rejected",
            player_id="quality-confidence-rejected",
            is_elite=False,
            quality_score=49.99,
            confidence=0.679,
            selection_score=85.0,
        ),
    ]

    with caplog.at_level(logging.INFO, logger="test_predict_pipeline_selectors"):
        result, config = _run_pipeline_with_candidates(monkeypatch, tmp_path, candidates)

    trace = _board_trace_from_caplog(caplog)
    telemetry_reasons = _elite_telemetry_reason_counts(config)

    assert result.elite_props.empty
    assert result.selected_props.empty
    assert result.summary["elite_count"] == 0
    assert result.summary["selected_count"] == 0
    assert result.summary["board_analytics"] == {
        "elite_count": 0,
        "overs_count": 0,
        "unders_count": 0,
        "avg_edge": 0.0,
        "avg_abs_edge": 0.0,
        "max_team_exposure": 0,
        "max_game_exposure": 0,
        "unique_teams": 0,
        "unique_games": 0,
    }
    assert list(result.full_market_props["player_name"]) == [
        "Market Rejected",
        "Negative Edge Rejected",
        "Quality Confidence Rejected",
    ]
    assert telemetry_reasons["market_filtered_by_elite_policy"] == 1
    assert telemetry_reasons[REJECT_NEGATIVE_EDGE_DIRECTION] == 1
    assert telemetry_reasons["reject_quality_confidence_threshold"] == 1
    assert trace["elite"]["candidate_count_entering_elite_selection"] == 3
    assert trace["elite"]["candidate_count_after_elite_admission_filter"] == 0
    assert trace["elite"]["candidate_count_after_concentration_caps"] == 0
    assert trace["elite"]["candidate_count_after_backfill"] == 0
    assert trace["elite"]["selected_count"] == 0


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


def test_nested_elite_selector_enforces_team_cap_before_final_elite_board(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidates = [
        _candidate(f"Team Cap Rank {rank}", player_id=f"team-cap-{rank}", team="AAA", team_abbr="AAA", game_id=9100 + rank, selection_score=100.0 - rank)
        for rank in range(1, 6)
    ]

    with caplog.at_level(logging.INFO, logger="test_predict_pipeline_selectors"):
        result, _ = _run_pipeline_with_candidates(
            monkeypatch,
            tmp_path,
            candidates,
            config_overrides={"elite_team_cap": 3, "elite_game_cap": 10, "elite_size": 10},
        )

    trace = _board_trace_from_caplog(caplog)
    assert list(result.elite_props["player_name"]) == [
        "Team Cap Rank 1",
        "Team Cap Rank 2",
        "Team Cap Rank 3",
    ]
    assert result.summary["elite_max_team_exposure"] == 3
    assert trace["elite"]["skipped_by_team_cap"] == 2
    assert trace["elite"]["skipped_by_game_cap"] == 0
    assert trace["elite"]["candidate_count_after_concentration_caps"] == 3
    assert result.elite_props["selection_rejection_reason"].fillna("").eq("").all()


def test_nested_elite_selector_default_board_limit_is_ten_after_caps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidates = [
        _candidate(
            f"Board Limit Rank {rank:02d}",
            player_id=f"board-limit-{rank}",
            team=f"T{rank:02d}",
            team_abbr=f"T{rank:02d}",
            game_id=9200 + rank,
            selection_score=100.0 - rank,
        )
        for rank in range(1, 13)
    ]

    with caplog.at_level(logging.INFO, logger="test_predict_pipeline_selectors"):
        result, config = _run_pipeline_with_candidates(
            monkeypatch,
            tmp_path,
            candidates,
            config_overrides={"elite_team_cap": 20, "elite_game_cap": 20},
        )

    thresholds = EliteThresholds.default()
    trace = _board_trace_from_caplog(caplog)
    assert thresholds.board_limit == 20
    assert not hasattr(config, "elite_size")
    assert list(result.elite_props["player_name"]) == [
        f"Board Limit Rank {rank:02d}" for rank in range(1, 11)
    ]
    assert len(result.full_market_props) == 12
    assert len(result.elite_props) == 10
    assert len(result.elite_props) != min(len(candidates), thresholds.board_limit)
    assert trace["elite"]["candidate_count_after_elite_admission_filter"] == 12
    assert trace["elite"]["candidate_count_after_concentration_caps"] == 12
    assert trace["elite"]["candidate_count_after_backfill"] == 10
    assert trace["elite"]["selected_count"] == 10


def test_nested_elite_selector_explicit_elite_size_override_controls_final_board(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidates = [
        _candidate(
            f"Override Limit Rank {rank:02d}",
            player_id=f"override-limit-{rank}",
            team=f"O{rank:02d}",
            team_abbr=f"O{rank:02d}",
            game_id=9250 + rank,
            selection_score=100.0 - rank,
        )
        for rank in range(1, 10)
    ]

    with caplog.at_level(logging.INFO, logger="test_predict_pipeline_selectors"):
        result, config = _run_pipeline_with_candidates(
            monkeypatch,
            tmp_path,
            candidates,
            config_overrides={"elite_team_cap": 20, "elite_game_cap": 20, "elite_size": 4},
        )

    trace = _board_trace_from_caplog(caplog)
    assert config.elite_size == 4
    assert list(result.elite_props["player_name"]) == [
        f"Override Limit Rank {rank:02d}" for rank in range(1, 5)
    ]
    assert len(result.elite_props) == 4
    assert trace["elite"]["candidate_count_after_elite_admission_filter"] == 9
    assert trace["elite"]["candidate_count_after_concentration_caps"] == 9
    assert trace["elite"]["candidate_count_after_backfill"] == 4
    assert trace["elite"]["selected_count"] == 4


def test_nested_elite_selector_final_board_limit_runs_after_game_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    candidates = [
        _candidate(
            f"Post Cap Rank {rank:02d}",
            player_id=f"post-cap-{rank}",
            team=f"G{rank:02d}",
            team_abbr=f"G{rank:02d}",
            game_id=9300 if rank <= 5 else 9300 + rank,
            selection_score=100.0 - rank,
        )
        for rank in range(1, 9)
    ]

    with caplog.at_level(logging.INFO, logger="test_predict_pipeline_selectors"):
        result, _ = _run_pipeline_with_candidates(
            monkeypatch,
            tmp_path,
            candidates,
            config_overrides={"elite_team_cap": 20, "elite_game_cap": 2, "elite_size": 3},
        )

    trace = _board_trace_from_caplog(caplog)
    assert list(result.elite_props["player_name"]) == [
        "Post Cap Rank 01",
        "Post Cap Rank 02",
        "Post Cap Rank 06",
    ]
    assert result.summary["elite_max_game_exposure"] == 2
    assert trace["elite"]["skipped_by_game_cap"] == 3
    assert trace["elite"]["candidate_count_after_elite_admission_filter"] == 8
    assert trace["elite"]["candidate_count_after_concentration_caps"] == 5
    assert trace["elite"]["candidate_count_after_backfill"] == 3
    assert trace["elite"]["selected_count"] == 3


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


def test_nested_elite_selector_output_keeps_kelly_and_operator_card_columns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    result, _ = _run_pipeline_with_candidates(
        monkeypatch,
        tmp_path,
        [
            _candidate(
                "Downstream Columns",
                player_id="downstream-columns",
                selection_score=99.0,
                stake_fraction=0.0125,
                recommended_bet=12.5,
            )
        ],
    )

    required_columns = {
        "prediction_date",
        "player_name",
        "entity_name",
        "team",
        "team_abbr",
        "opponent",
        "game_id",
        "market_type",
        "selection",
        "line",
        "sportsbook_line",
        "odds",
        "edge",
        "edge_pct",
        "side_edge",
        "side_edge_pct",
        "confidence",
        "quality_score",
        "selection_score",
        "stake_fraction",
        "recommended_bet",
        "is_live_market",
        "synthetic_line",
        "line_source",
        "source_lane",
        "qualification_reason",
        "selection_rejection_reason",
        "game_status",
        "game_datetime",
        "odds_updated_at",
    }
    missing = required_columns.difference(result.elite_props.columns)

    assert missing == set()
    assert len(result.elite_props) == 1
    row = result.elite_props.iloc[0]
    assert row["player_name"] == "Downstream Columns"
    assert row["odds"] == -110
    assert row["confidence"] == 0.82
    assert row["side_edge_pct"] == 0.08
    assert row["stake_fraction"] == 0.0125
    assert row["recommended_bet"] == 12.5
    assert row["selection_rejection_reason"] == ""
    assert result.summary["elite_max_game_exposure"] <= EliteThresholds.default().game_cap
