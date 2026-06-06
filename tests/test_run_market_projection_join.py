from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import scripts.run_market_projection_join as market_projection_join
from scripts.run_market_projection_join import (
    MARKET_PROJECTION_JOIN_NO_PROJECTION_SOURCE,
    MARKET_PROJECTION_JOIN_OK,
    MARKET_PROJECTION_JOIN_PARTIAL_MATCH,
    run_market_projection_join,
)


PREDICTION_DATE = "2026-06-05"


def _market_row(
    *,
    player_name: str = "Jane Doe",
    market_type: str = "player_points",
    side: str = "over",
    line: float = 25.5,
    sportsbook: str = "DraftKings",
    eligible_for_betting: bool = False,
) -> dict[str, Any]:
    return {
        "provider": "the_odds_api",
        "provider_event_id": "evt_target",
        "home_team": "Oklahoma City Thunder",
        "away_team": "Indiana Pacers",
        "game_date": PREDICTION_DATE,
        "commence_time_utc": "2026-06-05T23:30:00Z",
        "commence_time_local": "2026-06-05T19:30:00-04:00",
        "player_name": player_name,
        "market_type": market_type,
        "side": side,
        "line": line,
        "american_odds": -110,
        "sportsbook": sportsbook,
        "updated_at": "2026-06-05T14:01:00Z",
        "source": "the_odds_api:event_odds",
        "eligible_for_betting": eligible_for_betting,
    }


def _runtime_root(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime"


def _output_dir(tmp_path: Path) -> Path:
    return _runtime_root(tmp_path) / "research"


def _diagnostics_dir(tmp_path: Path) -> Path:
    return _runtime_root(tmp_path) / "diagnostics"


def _market_board_path(tmp_path: Path) -> Path:
    return _output_dir(tmp_path) / f"market_validation_board_{PREDICTION_DATE}.csv"


def _write_market_board(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = _market_board_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _write_stat_projection_source(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = _output_dir(tmp_path) / f"stat_projection_source_{PREDICTION_DATE}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _read_diagnostics(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run(tmp_path: Path, *, projection_source: Path | None = None):
    return run_market_projection_join(
        target_date=PREDICTION_DATE,
        market_board=_market_board_path(tmp_path),
        projection_source=projection_source,
        output_dir=_output_dir(tmp_path),
        diagnostics_dir=_diagnostics_dir(tmp_path),
    )


def test_clean_join_maps_projection_values_and_edges_for_supported_markets(
    tmp_path: Path,
) -> None:
    _write_market_board(
        tmp_path,
        [
            _market_row(market_type="player_points", side="over", line=25.5),
            _market_row(market_type="player_points", side="under", line=25.5),
            _market_row(market_type="player_rebounds", side="over", line=8.5),
            _market_row(market_type="player_assists", side="under", line=6.5),
        ],
    )
    _write_stat_projection_source(
        tmp_path,
        [
            {
                "player_name": "jane doe",
                "points": 27.0,
                "reb": 9.0,
                "projected_assists": 5.0,
                "minutes": 33.0,
                "team_abbreviation": "OKC",
                "source": "api_nba",
            }
        ],
    )

    result = _run(tmp_path)

    assert result.status == MARKET_PROJECTION_JOIN_OK
    joined = pd.read_csv(result.output_path)
    assert len(joined.index) == 4
    assert joined["matched_projection_player_name"].tolist() == ["jane doe"] * 4
    assert joined["projection_match_status"].tolist() == ["matched"] * 4
    assert joined["eligible_for_betting"].tolist() == [False] * 4

    points_over = joined[(joined["market_type"] == "player_points") & (joined["side"] == "over")].iloc[0]
    points_under = joined[(joined["market_type"] == "player_points") & (joined["side"] == "under")].iloc[0]
    rebounds = joined[joined["market_type"] == "player_rebounds"].iloc[0]
    assists = joined[joined["market_type"] == "player_assists"].iloc[0]

    assert points_over["projection_value"] == 27.0
    assert points_over["raw_edge"] == 1.5
    assert points_over["side_adjusted_edge"] == 1.5
    assert points_under["raw_edge"] == 1.5
    assert points_under["side_adjusted_edge"] == -1.5
    assert rebounds["projection_value"] == 9.0
    assert assists["projection_value"] == 5.0
    assert assists["side_adjusted_edge"] == 1.5
    assert joined["projection_minutes"].tolist() == [33.0] * 4

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["status"] == MARKET_PROJECTION_JOIN_OK
    assert diagnostics["market_row_count"] == 4
    assert diagnostics["joined_row_count"] == 4
    assert diagnostics["matched_player_count"] == 1
    assert diagnostics["unmatched_player_count"] == 0
    assert diagnostics["projection_value_available_count"] == 4
    assert diagnostics["markets_with_projection_values"] == [
        "player_points",
        "player_rebounds",
        "player_assists",
    ]
    assert diagnostics["eligible_for_betting_any_true"] is False
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False
    assert diagnostics["operator_betting_boards_written"] == []
    assert not hasattr(market_projection_join, "MarketProp")


def test_apostrophe_and_hyphen_name_normalization_works(tmp_path: Path) -> None:
    _write_market_board(
        tmp_path,
        [
            _market_row(player_name="D'Angelo Russell", line=16.5),
            _market_row(player_name="Karl-Anthony Towns", line=22.5),
        ],
    )
    _write_stat_projection_source(
        tmp_path,
        [
            {"player_name": "DAngelo Russell", "pts": 18.0},
            {"player_name": "Karl Anthony Towns", "points_projection": 24.0},
        ],
    )

    result = _run(tmp_path)

    assert result.status == MARKET_PROJECTION_JOIN_OK
    joined = pd.read_csv(result.output_path)
    assert joined["projection_match_status"].tolist() == ["matched", "matched"]
    assert joined["projection_value"].tolist() == [18.0, 24.0]
    assert joined["matched_projection_player_name"].tolist() == [
        "DAngelo Russell",
        "Karl Anthony Towns",
    ]


def test_unmatched_players_are_diagnosed_with_partial_match_status(tmp_path: Path) -> None:
    _write_market_board(
        tmp_path,
        [
            _market_row(player_name="Matched Player", line=20.5),
            _market_row(player_name="Missing Player", line=12.5),
        ],
    )
    _write_stat_projection_source(
        tmp_path,
        [{"player_name": "Matched Player", "points": 21.0}],
    )

    result = _run(tmp_path)

    assert result.status == MARKET_PROJECTION_JOIN_PARTIAL_MATCH
    joined = pd.read_csv(result.output_path)
    assert joined["projection_match_status"].tolist() == ["matched", "unmatched_player"]

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["matched_player_count"] == 1
    assert diagnostics["unmatched_player_count"] == 1
    assert diagnostics["unmatched_players"] == ["Missing Player"]
    assert diagnostics["projection_value_available_count"] == 1
    assert diagnostics["projection_value_missing_count"] == 1


def test_missing_projection_source_is_non_fatal(tmp_path: Path) -> None:
    _write_market_board(tmp_path, [_market_row(player_name="Jane Doe")])

    result = _run(tmp_path)

    assert result.status == MARKET_PROJECTION_JOIN_NO_PROJECTION_SOURCE
    assert result.output_path.exists()
    assert result.summary_path.exists()
    assert result.diagnostics_path.exists()

    joined = pd.read_csv(result.output_path)
    assert joined["projection_match_status"].tolist() == ["projection_source_unavailable"]
    assert joined["eligible_for_betting"].tolist() == [False]

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["projection_source_available"] is False
    assert diagnostics["projection_source_path"] == ""
    assert diagnostics["matched_player_count"] == 0
    assert diagnostics["unmatched_players"] == ["Jane Doe"]
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False


def test_baseline_fallback_attaches_baseline_recent_and_minutes_context(
    tmp_path: Path,
) -> None:
    _write_market_board(
        tmp_path,
        [
            _market_row(market_type="player_points"),
            _market_row(market_type="player_rebounds", line=7.5),
            _market_row(market_type="player_assists", line=5.5),
        ],
    )
    baseline_path = tmp_path / "outputs" / "model" / "player_baselines.csv"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "player_name": "Jane Doe",
                "team_abbr": "OKC",
                "pts_avg": 24.0,
                "pts_recent": 26.0,
                "reb_avg": 8.0,
                "reb_recent": 9.0,
                "ast_avg": 6.0,
                "ast_recent": 7.0,
                "min_avg": 32.0,
                "min_recent": 34.0,
            }
        ]
    ).to_csv(baseline_path, index=False)

    result = _run(tmp_path)

    assert result.status == MARKET_PROJECTION_JOIN_OK
    joined = pd.read_csv(result.output_path)
    assert joined["baseline_value"].tolist() == [24.0, 8.0, 6.0]
    assert joined["recent_avg_value"].tolist() == [26.0, 9.0, 7.0]
    assert joined["projection_min_avg"].tolist() == [32.0, 32.0, 32.0]
    assert joined["projection_min_recent"].tolist() == [34.0, 34.0, 34.0]
    assert result.diagnostics["projection_source_path"] == str(baseline_path)


def test_no_kelly_elite_or_operator_artifacts_are_written(tmp_path: Path) -> None:
    runtime_root = _runtime_root(tmp_path)
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True)
    kelly_sentinel = operator_dir / f"kelly_stakes_{PREDICTION_DATE}.csv"
    elite_sentinel = operator_dir / f"elite_board_{PREDICTION_DATE}.csv"
    kelly_sentinel.write_text("player_name,stake\nExisting,10\n", encoding="utf-8")
    elite_sentinel.write_text("player_name,score\nExisting,99\n", encoding="utf-8")

    _write_market_board(tmp_path, [_market_row(), _market_row(side="under")])
    _write_stat_projection_source(tmp_path, [{"player_name": "Jane Doe", "points": 27.0}])

    result = _run(tmp_path)

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False
    assert diagnostics["operator_artifacts_written"] == []
    assert diagnostics["operator_betting_boards_written"] == []
    assert kelly_sentinel.read_text(encoding="utf-8") == "player_name,stake\nExisting,10\n"
    assert elite_sentinel.read_text(encoding="utf-8") == "player_name,score\nExisting,99\n"
    assert not (operator_dir / f"full_market_board_{PREDICTION_DATE}.csv").exists()
