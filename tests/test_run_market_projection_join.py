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


def _write_cleaned_projection_context(
    tmp_path: Path,
    rows: list[dict[str, Any]],
) -> Path:
    path = _output_dir(tmp_path) / f"projection_context_clean_{PREDICTION_DATE}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _write_baseline_source(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "outputs" / "model" / "player_baselines.csv"
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
    assert joined["projection_match_status"].tolist() == ["team_aware_matched"] * 4
    assert joined["eligible_for_betting"].tolist() == [False] * 4

    points_over = joined[(joined["market_type"] == "player_points") & (joined["side"] == "over")].iloc[0]
    points_under = joined[(joined["market_type"] == "player_points") & (joined["side"] == "under")].iloc[0]
    rebounds = joined[joined["market_type"] == "player_rebounds"].iloc[0]
    assists = joined[joined["market_type"] == "player_assists"].iloc[0]

    assert points_over["projection_value"] == 27.0
    assert points_over["projection_source_type"] == "model_projection"
    assert points_over["projection_quality_flag"] == "projection_available"
    assert points_over["raw_edge"] == 1.5
    assert points_over["side_adjusted_edge"] == 1.5
    assert points_over["edge_direction"] == "over_edge"
    assert points_over["abs_edge"] == 1.5
    assert points_over["edge_bucket"] == "medium_edge"
    assert points_under["raw_edge"] == 1.5
    assert points_under["side_adjusted_edge"] == -1.5
    assert points_under["edge_direction"] == "no_edge"
    assert rebounds["projection_value"] == 9.0
    assert assists["projection_value"] == 5.0
    assert assists["side_adjusted_edge"] == 1.5
    assert assists["edge_direction"] == "under_edge"
    assert joined["projection_minutes"].tolist() == [33.0] * 4

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["status"] == MARKET_PROJECTION_JOIN_OK
    assert diagnostics["market_row_count"] == 4
    assert diagnostics["joined_row_count"] == 4
    assert diagnostics["matched_player_count"] == 1
    assert diagnostics["team_aware_match_count"] == 4
    assert diagnostics["name_only_match_count"] == 0
    assert diagnostics["unmatched_player_count"] == 0
    assert diagnostics["projection_source_type"] == "stat_projection_source"
    assert diagnostics["used_cleaned_projection_context"] is False
    assert diagnostics["duplicate_normalized_player_warning_count"] == 0
    assert diagnostics["projection_value_available_count"] == 4
    assert diagnostics["projection_value_missing_count"] == 0
    assert diagnostics["projection_source_type_counts"] == {"model_projection": 4}
    assert diagnostics["projection_quality_flag_counts"] == {
        "projection_available": 4
    }
    assert diagnostics["edge_available_count"] == 4
    assert diagnostics["edge_missing_count"] == 0
    assert diagnostics["edge_bucket_counts"] == {
        "medium_edge": 3,
        "small_edge": 1,
    }
    assert diagnostics["positive_side_adjusted_edge_count"] == 3
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


def test_projection_fallback_precedence_and_unavailable_context(tmp_path: Path) -> None:
    _write_market_board(
        tmp_path,
        [
            _market_row(player_name="Model Player", line=19.0),
            _market_row(player_name="Recent Player", line=11.0),
            _market_row(player_name="Baseline Player", side="under", line=15.0),
            _market_row(player_name="No Context Player", line=8.0),
        ],
    )
    _write_stat_projection_source(
        tmp_path,
        [
            {
                "player_name": "Model Player",
                "points": 20.0,
                "pts_recent": 18.0,
                "pts_avg": 17.0,
            },
            {
                "player_name": "Recent Player",
                "pts_recent": 12.0,
                "pts_avg": 10.0,
            },
            {
                "player_name": "Baseline Player",
                "pts_avg": 14.0,
            },
            {
                "player_name": "No Context Player",
                "minutes": 30.0,
            },
        ],
    )

    result = _run(tmp_path)

    assert result.status == MARKET_PROJECTION_JOIN_OK
    joined = pd.read_csv(result.output_path).set_index("player_name")

    assert joined.loc["Model Player", "projection_value"] == 20.0
    assert joined.loc["Recent Player", "projection_value"] == 12.0
    assert joined.loc["Baseline Player", "projection_value"] == 14.0
    assert pd.isna(joined.loc["No Context Player", "projection_value"])

    assert joined["projection_source_type"].tolist() == [
        "model_projection",
        "recent_avg_fallback",
        "baseline_fallback",
        "unavailable",
    ]
    assert joined["projection_quality_flag"].tolist() == [
        "projection_available",
        "fallback_recent_average",
        "fallback_baseline_only",
        "no_projection_context",
    ]
    assert joined.loc["Recent Player", "raw_edge"] == 1.0
    assert joined.loc["Recent Player", "side_adjusted_edge"] == 1.0
    assert joined.loc["Recent Player", "edge_direction"] == "over_edge"
    assert joined.loc["Baseline Player", "raw_edge"] == -1.0
    assert joined.loc["Baseline Player", "side_adjusted_edge"] == 1.0
    assert joined.loc["Baseline Player", "edge_direction"] == "under_edge"
    assert pd.isna(joined.loc["No Context Player", "raw_edge"])
    assert joined.loc["No Context Player", "edge_direction"] == "unavailable"
    assert joined.loc["No Context Player", "edge_bucket"] == "unavailable"
    assert joined["eligible_for_betting"].tolist() == [False] * 4

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["projection_value_available_count"] == 3
    assert diagnostics["projection_value_missing_count"] == 1
    assert diagnostics["projection_source_type_counts"] == {
        "model_projection": 1,
        "recent_avg_fallback": 1,
        "baseline_fallback": 1,
        "unavailable": 1,
    }
    assert diagnostics["projection_quality_flag_counts"] == {
        "projection_available": 1,
        "fallback_recent_average": 1,
        "fallback_baseline_only": 1,
        "no_projection_context": 1,
    }
    assert diagnostics["edge_available_count"] == 3
    assert diagnostics["edge_missing_count"] == 1
    assert diagnostics["positive_side_adjusted_edge_count"] == 3

    summary = result.summary_path.read_text(encoding="utf-8")
    assert "projection_source_type_counts:" in summary
    assert "edge_bucket_counts:" in summary
    assert "positive_side_adjusted_edge_count: 3" in summary
    assert (
        "WARNING: Research-only edge preview. Fallback projections are not betting-approved."
        in summary
    )


def test_edge_direction_and_bucket_boundaries(tmp_path: Path) -> None:
    _write_market_board(
        tmp_path,
        [
            _market_row(line=9.75, sportsbook="Tiny"),
            _market_row(line=9.5, sportsbook="Small"),
            _market_row(side="under", line=11.5, sportsbook="Medium"),
            _market_row(line=7.0, sportsbook="Large"),
            _market_row(side="under", line=9.0, sportsbook="NoEdge"),
        ],
    )
    _write_stat_projection_source(
        tmp_path,
        [{"player_name": "Jane Doe", "points": 10.0}],
    )

    result = _run(tmp_path)

    joined = pd.read_csv(result.output_path).set_index("sportsbook")
    assert joined["abs_edge"].tolist() == [0.25, 0.5, 1.5, 3.0, 1.0]
    assert joined["edge_bucket"].tolist() == [
        "tiny_edge",
        "small_edge",
        "medium_edge",
        "large_edge",
        "small_edge",
    ]
    assert joined["edge_direction"].tolist() == [
        "over_edge",
        "over_edge",
        "under_edge",
        "over_edge",
        "no_edge",
    ]

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["edge_bucket_counts"] == {
        "tiny_edge": 1,
        "small_edge": 2,
        "medium_edge": 1,
        "large_edge": 1,
    }
    assert diagnostics["positive_side_adjusted_edge_count"] == 4


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
    assert joined["projection_match_status"].tolist() == [
        "name_only_matched",
        "name_only_matched",
    ]
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
    assert joined["projection_match_status"].tolist() == [
        "name_only_matched",
        "unmatched",
    ]

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
    assert diagnostics["projection_source_type"] == "unavailable"
    assert diagnostics["used_cleaned_projection_context"] is False
    assert diagnostics["matched_player_count"] == 0
    assert diagnostics["team_aware_match_count"] == 0
    assert diagnostics["name_only_match_count"] == 0
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
    baseline_path = _write_baseline_source(
        tmp_path,
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
        ],
    )

    result = _run(tmp_path)

    assert result.status == MARKET_PROJECTION_JOIN_OK
    joined = pd.read_csv(result.output_path)
    assert joined["baseline_value"].tolist() == [24.0, 8.0, 6.0]
    assert joined["recent_avg_value"].tolist() == [26.0, 9.0, 7.0]
    assert joined["projection_value"].tolist() == [26.0, 9.0, 7.0]
    assert joined["projection_source_type"].tolist() == ["recent_avg_fallback"] * 3
    assert joined["projection_quality_flag"].tolist() == [
        "fallback_recent_average"
    ] * 3
    assert joined["projection_min_avg"].tolist() == [32.0, 32.0, 32.0]
    assert joined["projection_min_recent"].tolist() == [34.0, 34.0, 34.0]
    assert result.diagnostics["projection_source_path"] == str(baseline_path)
    assert result.diagnostics["projection_source_type"] == "raw_player_baselines"
    assert result.diagnostics["team_aware_match_count"] == 3


def test_cleaned_projection_context_is_preferred_over_raw_baselines(
    tmp_path: Path,
) -> None:
    _write_market_board(tmp_path, [_market_row()])
    cleaned_path = _write_cleaned_projection_context(
        tmp_path,
        [
            {
                "player_name": "Jane Doe",
                "team_abbr": "OKC",
                "pts_avg": 26.0,
                "pts_recent": 28.0,
            }
        ],
    )
    _write_baseline_source(
        tmp_path,
        [
            {
                "player_name": "Jane Doe",
                "team_abbr": "OKC",
                "pts_avg": 19.0,
                "pts_recent": 20.0,
            }
        ],
    )
    _write_stat_projection_source(
        tmp_path,
        [{"player_name": "Jane Doe", "team_abbr": "OKC", "points": 24.0}],
    )

    result = _run(tmp_path)

    joined = pd.read_csv(result.output_path)
    assert joined["projection_value"].tolist() == [28.0]
    assert joined["projection_match_status"].tolist() == ["team_aware_matched"]
    assert result.diagnostics["projection_source_path"] == str(cleaned_path)
    assert result.diagnostics["projection_source_type"] == "cleaned_projection_context"
    assert result.diagnostics["used_cleaned_projection_context"] is True
    assert result.diagnostics["duplicate_normalized_player_warning_count"] == 0

    summary = result.summary_path.read_text(encoding="utf-8")
    assert f"projection_source_path: {cleaned_path}" in summary
    assert "projection_source_type: cleaned_projection_context" in summary
    assert "used_cleaned_projection_context: True" in summary
    assert "team_aware_match_count: 1" in summary
    assert "name_only_match_count: 0" in summary
    assert "unmatched_player_count: 0" in summary
    assert "duplicate_normalized_player_warning_count: 0" in summary


def test_explicit_projection_source_overrides_cleaned_context(tmp_path: Path) -> None:
    _write_market_board(tmp_path, [_market_row()])
    _write_cleaned_projection_context(
        tmp_path,
        [{"player_name": "Jane Doe", "team_abbr": "OKC", "pts_recent": 28.0}],
    )
    explicit_path = tmp_path / "explicit_projection_source.csv"
    pd.DataFrame(
        [{"player_name": "Jane Doe", "team_abbr": "OKC", "points": 31.0}]
    ).to_csv(explicit_path, index=False)

    result = _run(tmp_path, projection_source=explicit_path)

    joined = pd.read_csv(result.output_path)
    assert joined["projection_value"].tolist() == [31.0]
    assert result.diagnostics["projection_source_path"] == str(explicit_path)
    assert result.diagnostics["projection_source_type"] == "explicit_projection_source"
    assert result.diagnostics["used_cleaned_projection_context"] is False


def test_team_aware_match_wins_for_duplicate_normalized_name(tmp_path: Path) -> None:
    _write_market_board(tmp_path, [_market_row(player_name="Shared Name")])
    _write_stat_projection_source(
        tmp_path,
        [
            {"player_name": "Shared Name", "team_abbr": "BOS", "points": 10.0},
            {"player_name": "Shared Name", "team_abbr": "OKC", "points": 27.0},
        ],
    )

    result = _run(tmp_path)

    joined = pd.read_csv(result.output_path)
    assert joined["projection_value"].tolist() == [27.0]
    assert joined["projection_team_abbr"].tolist() == ["OKC"]
    assert joined["projection_match_status"].tolist() == ["team_aware_matched"]
    assert result.diagnostics["team_aware_match_count"] == 1
    assert result.diagnostics["name_only_match_count"] == 0
    assert result.diagnostics["duplicate_normalized_player_warning_count"] == 1


def test_name_only_match_is_used_when_projection_team_is_not_in_game(
    tmp_path: Path,
) -> None:
    _write_market_board(tmp_path, [_market_row()])
    _write_stat_projection_source(
        tmp_path,
        [{"player_name": "Jane Doe", "team_abbr": "BOS", "points": 27.0}],
    )

    result = _run(tmp_path)

    joined = pd.read_csv(result.output_path)
    assert joined["projection_match_status"].tolist() == ["name_only_matched"]
    assert joined["projection_value"].tolist() == [27.0]
    assert result.diagnostics["team_aware_match_count"] == 0
    assert result.diagnostics["name_only_match_count"] == 1


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
