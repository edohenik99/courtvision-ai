from __future__ import annotations

from pathlib import Path

import pandas as pd

from courtvision.reporting.paper_kelly_simulation import (
    GAME_EXPOSURE_CAP,
    PAPER_BUCKET_EXPOSURE_CAP,
    PLAYER_EXPOSURE_CAP,
    PLAYER_SELECTION_EXPOSURE_CAP,
    REPORT_COLUMNS,
    ROW_STAKE_CAP,
    SIDE_EXPOSURE_CAP,
    SIMULATION_WARNING,
    TEAM_EXPOSURE_CAP,
    TOTAL_PAPER_EXPOSURE_CAP,
    build_paper_kelly_simulation,
    write_paper_kelly_simulation,
)
from courtvision.reporting.paper_kelly_performance import REPORT_TITLE as PAPER_KELLY_PERFORMANCE_TITLE
from scripts.write_daily_summary import write_daily_summary_outputs


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _combo_row(
    player_name: str = "Combo Under",
    *,
    edge: float = -2.5,
    selection: str = "under",
) -> dict:
    return {
        "prediction_date": "2026-05-06",
        "player_name": player_name,
        "team_abbr": "BOS",
        "opponent": "PHI",
        "market_type": "player_points_rebounds_assists",
        "selection": selection,
        "line": 36.5,
        "odds": -110,
        "edge": edge,
        "confidence": 0.75,
        "quality_score": 82.0,
        "context_pick_alignment": "aligned",
        "context_caution_level": "low",
    }


def _high_caution_row(player_name: str = "High Caution Over", *, edge: float = 4.5) -> dict:
    return {
        "prediction_date": "2026-05-06",
        "player_name": player_name,
        "team_abbr": "NYK",
        "opponent": "PHI",
        "market_type": "player_points",
        "selection": "over",
        "line": 27.5,
        "odds": -115,
        "edge": edge,
        "confidence": 0.80,
        "quality_score": 88.0,
        "context_pick_alignment": "conflicted",
        "context_caution_level": "high",
        "kelly_projected_skip_reason": "context_high_caution_over",
        "final_elite_rejection_reason": "elite_reject_context_high_caution_over",
    }


def _high_caution_row_for(
    index: int,
    *,
    player_name: str | None = None,
    team_abbr: str = "NYK",
    opponent: str = "PHI",
    selection: str = "over",
    edge: float = 8.0,
) -> dict:
    row = _high_caution_row(player_name or f"High Caution {index}", edge=edge)
    row["team_abbr"] = team_abbr
    row["opponent"] = opponent
    row["selection"] = selection
    row["market_type"] = f"player_points_{index}" if index else "player_points"
    row["line"] = 10.5 + index
    return row


def _kelly_row() -> dict:
    return {
        "prediction_date": "2026-05-06",
        "player_name": "Real Kelly",
        "market_type": "player_points",
        "selection": "under",
        "stake_amount": 12.0,
        "expected_value": 1.25,
        "eligible": True,
    }


def test_paper_kelly_file_generated(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    _write_csv(operator / f"combo_under_watchlist_{prediction_date}.csv", [_combo_row()])
    _write_csv(operator / f"high_caution_over_watchlist_{prediction_date}.csv", [_high_caution_row()])

    text_path, csv_path, report = write_paper_kelly_simulation(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    assert text_path == operator / f"paper_kelly_simulation_{prediction_date}.txt"
    assert csv_path == operator / f"paper_kelly_simulation_{prediction_date}.csv"
    assert text_path.exists()
    assert csv_path.exists()
    assert len(report) == 2
    assert list(pd.read_csv(csv_path).columns) == list(REPORT_COLUMNS)
    assert "total paper rows: 2" in text_path.read_text(encoding="utf-8")


def test_over_with_positive_edge_gets_paper_stake() -> None:
    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        high_caution_over_watchlist=pd.DataFrame([_high_caution_row(edge=3.0)]),
    )

    row = report.iloc[0]
    assert row["edge"] == 3.0
    assert row["directional_edge"] == 3.0
    assert row["simulated_stake"] > 0


def test_under_with_negative_edge_gets_paper_stake() -> None:
    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        combo_under_watchlist=pd.DataFrame([_combo_row(edge=-2.0)]),
    )

    row = report.iloc[0]
    assert row["edge"] == -2.0
    assert row["directional_edge"] == 2.0
    assert row["simulated_stake"] > 0


def test_under_with_positive_edge_gets_no_paper_stake() -> None:
    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        combo_under_watchlist=pd.DataFrame([_combo_row(edge=2.0)]),
    )

    row = report.iloc[0]
    assert row["edge"] == 2.0
    assert row["directional_edge"] == -2.0
    assert row["simulated_stake"] == 0.0
    assert row["simulated_ev"] == 0.0


def test_paper_kelly_caps_and_real_kelly_flags() -> None:
    rows = [_combo_row("Same Player"), _combo_row("Same Player"), _high_caution_row("Other Player")]

    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        combo_under_watchlist=pd.DataFrame(rows[:2]),
        high_caution_over_watchlist=pd.DataFrame(rows[2:]),
    )

    assert not report.empty
    assert pd.to_numeric(report["simulated_stake"], errors="coerce").max() <= ROW_STAKE_CAP
    by_player = report.groupby("player_name")["simulated_stake"].sum()
    assert float(by_player.max()) <= PLAYER_EXPOSURE_CAP
    by_game = report.assign(game="PHI").groupby("game")["simulated_stake"].sum()
    assert float(by_game.max()) <= GAME_EXPOSURE_CAP
    assert set(report["real_kelly_eligible"].astype(str).str.lower()) == {"false"}
    assert set(report["simulation_only"].astype(str).str.lower()) == {"true"}


def test_repeated_player_rows_are_capped_by_player_direction() -> None:
    rows = [
        _high_caution_row_for(index, player_name="Same Player", team_abbr=f"T{index}", opponent=f"O{index}")
        for index in range(6)
    ]

    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        high_caution_over_watchlist=pd.DataFrame(rows),
    )

    player_exposure = report.groupby("player_name")["simulated_stake"].sum()
    player_side_exposure = report.groupby(["player_name", "selection"])["simulated_stake"].sum()
    reasons = ";".join(report["cap_adjustment_reason"].astype(str).tolist())
    assert float(player_exposure.loc["Same Player"]) <= PLAYER_EXPOSURE_CAP
    assert float(player_side_exposure.loc[("Same Player", "over")]) <= PLAYER_SELECTION_EXPOSURE_CAP
    assert "player_selection_exposure_cap" in reasons


def test_repeated_game_rows_are_capped() -> None:
    high_rows = [
        _high_caution_row_for(index, team_abbr=("BOS" if index % 2 == 0 else "NYK"), opponent=("NYK" if index % 2 == 0 else "BOS"))
        for index in range(4)
    ]
    combo_rows = [
        _combo_row(f"Combo Game {index}", edge=-8.0)
        | {
            "team_abbr": "BOS" if index % 2 == 0 else "NYK",
            "opponent": "NYK" if index % 2 == 0 else "BOS",
            "line": 20.5 + index,
        }
        for index in range(4)
    ]

    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        combo_under_watchlist=pd.DataFrame(combo_rows),
        high_caution_over_watchlist=pd.DataFrame(high_rows),
    )

    assert float(report["simulated_stake"].sum()) <= GAME_EXPOSURE_CAP + 1e-9
    assert pd.to_numeric(report["game_exposure_after_cap"], errors="coerce").max() <= GAME_EXPOSURE_CAP + 1e-9
    assert "game_exposure_cap" in ";".join(report["cap_adjustment_reason"].astype(str).tolist())


def test_repeated_team_rows_are_capped() -> None:
    rows = [
        _high_caution_row_for(index, team_abbr="NYK", opponent=f"O{index}", selection="over")
        for index in range(8)
    ]

    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        high_caution_over_watchlist=pd.DataFrame(rows),
    )

    team_exposure = report.loc[report["team_abbr"] == "NYK", "simulated_stake"].sum()
    assert float(team_exposure) <= TEAM_EXPOSURE_CAP + 1e-9
    assert pd.to_numeric(report["team_exposure_after_cap"], errors="coerce").max() <= TEAM_EXPOSURE_CAP + 1e-9
    assert "team_exposure_cap" in ";".join(report["cap_adjustment_reason"].astype(str).tolist())


def test_side_concentration_is_capped() -> None:
    rows = [
        _high_caution_row_for(index, team_abbr=f"T{index}", opponent=f"O{index}", selection="over")
        for index in range(8)
    ]

    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        high_caution_over_watchlist=pd.DataFrame(rows),
    )

    over_exposure = report.loc[report["selection"] == "over", "simulated_stake"].sum()
    assert float(over_exposure) <= SIDE_EXPOSURE_CAP + 1e-9
    assert pd.to_numeric(report["side_exposure_after_cap"], errors="coerce").max() <= SIDE_EXPOSURE_CAP + 1e-9
    assert "side_exposure_cap" in ";".join(report["cap_adjustment_reason"].astype(str).tolist())


def test_paper_bucket_exposure_is_capped() -> None:
    rows = [
        _high_caution_row_for(
            index,
            team_abbr=f"T{index}",
            opponent=f"O{index}",
            selection=("over" if index % 2 == 0 else "milestone"),
        )
        for index in range(8)
    ]

    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        high_caution_over_watchlist=pd.DataFrame(rows),
    )

    bucket_exposure = report.loc[
        report["paper_bucket"] == "high_caution_over_watchlist",
        "simulated_stake",
    ].sum()
    assert float(bucket_exposure) <= PAPER_BUCKET_EXPOSURE_CAP + 1e-9
    assert pd.to_numeric(report["bucket_exposure_after_cap"], errors="coerce").max() <= PAPER_BUCKET_EXPOSURE_CAP + 1e-9
    assert "paper_bucket_exposure_cap" in ";".join(report["cap_adjustment_reason"].astype(str).tolist())


def test_combo_under_watchlist_contributes_positive_exposure_when_favorable() -> None:
    combo = _combo_row("Favorable Combo", edge=-2.5)
    combo["team_abbr"] = "NYK"
    combo["opponent"] = "PHI"
    high_rows = [_high_caution_row(f"High Caution {index}", edge=6.0) for index in range(8)]

    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        combo_under_watchlist=pd.DataFrame([combo]),
        high_caution_over_watchlist=pd.DataFrame(high_rows),
    )

    combo_exposure = report.loc[
        report["paper_bucket"] == "combo_under_watchlist",
        "simulated_stake",
    ].sum()
    total_exposure = report["simulated_stake"].sum()
    assert combo_exposure > 0
    assert total_exposure <= GAME_EXPOSURE_CAP


def test_high_caution_over_watchlist_receives_exposure_but_is_capped() -> None:
    rows = [
        _high_caution_row_for(index, team_abbr=f"T{index}", opponent=f"O{index}", selection="over")
        for index in range(8)
    ]

    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        high_caution_over_watchlist=pd.DataFrame(rows),
    )

    high_exposure = report.loc[
        report["paper_bucket"] == "high_caution_over_watchlist",
        "simulated_stake",
    ].sum()
    assert high_exposure > 0
    assert high_exposure <= PAPER_BUCKET_EXPOSURE_CAP + 1e-9
    assert high_exposure <= SIDE_EXPOSURE_CAP + 1e-9


def test_total_paper_exposure_cap_still_applies() -> None:
    combo_rows = [
        _combo_row(f"Combo {index}", edge=-8.0)
        | {"team_abbr": f"C{index}", "opponent": f"D{index}", "line": 20.5 + index}
        for index in range(12)
    ]
    high_rows = [
        _high_caution_row_for(index, team_abbr=f"T{index}", opponent=f"O{index}", selection="over")
        for index in range(12)
    ]

    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        combo_under_watchlist=pd.DataFrame(combo_rows),
        high_caution_over_watchlist=pd.DataFrame(high_rows),
    )

    assert float(report["simulated_stake"].sum()) <= TOTAL_PAPER_EXPOSURE_CAP + 1e-9
    assert float(report["pre_cap_simulated_stake"].sum()) >= float(report["simulated_stake"].sum())


def test_real_kelly_file_unchanged_by_paper_simulation(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    kelly_path = operator / f"kelly_stakes_{prediction_date}.csv"
    _write_csv(operator / f"combo_under_watchlist_{prediction_date}.csv", [_combo_row()])
    _write_csv(operator / f"high_caution_over_watchlist_{prediction_date}.csv", [_high_caution_row()])
    _write_csv(kelly_path, [_kelly_row()])
    before = kelly_path.read_bytes()

    write_paper_kelly_simulation(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    assert kelly_path.read_bytes() == before


def test_combo_markets_remain_not_real_kelly_eligible() -> None:
    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        combo_under_watchlist=pd.DataFrame([_combo_row()]),
        high_caution_over_watchlist=pd.DataFrame(),
    )

    row = report.iloc[0]
    assert row["market_type"] == "player_points_rebounds_assists"
    assert str(row["real_kelly_eligible"]).lower() == "false"
    assert row["reason_not_real_kelly"] == "combo_market_not_real_kelly_eligible"


def test_high_caution_overs_remain_not_real_kelly_eligible() -> None:
    report = build_paper_kelly_simulation(
        prediction_date="2026-05-06",
        combo_under_watchlist=pd.DataFrame(),
        high_caution_over_watchlist=pd.DataFrame([_high_caution_row()]),
    )

    row = report.iloc[0]
    assert row["selection"] == "over"
    assert row["context_caution_level"] == "high"
    assert str(row["real_kelly_eligible"]).lower() == "false"
    assert row["reason_not_real_kelly"] == "high_caution_over_context_blocked"


def test_daily_summary_includes_paper_kelly_simulation_section(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=["prediction_date", "player_name"])
    _write_csv(operator / f"kelly_stakes_{prediction_date}.csv", [_kelly_row()])
    _write_csv(
        operator / f"full_market_board_{prediction_date}.csv",
        [
            _combo_row(),
            _high_caution_row(),
        ],
    )

    output_path, metadata = write_daily_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert "Paper Kelly Simulation" in text
    assert SIMULATION_WARNING in text
    assert PAPER_KELLY_PERFORMANCE_TITLE in text
    assert metadata["paper_kelly_simulation_count"] == 2
    assert metadata["paper_kelly_performance_current_date_rows"] == 2
    assert Path(metadata["paper_kelly_simulation_path"]).exists()
    assert Path(metadata["paper_kelly_simulation_csv_path"]).exists()
    assert Path(metadata["paper_kelly_history_path"]).exists()
    assert Path(metadata["paper_kelly_performance_report_path"]).exists()
    assert Path(metadata["paper_kelly_performance_report_csv_path"]).exists()
    assert pd.read_csv(operator / f"kelly_stakes_{prediction_date}.csv").equals(pd.DataFrame([_kelly_row()]))


def test_candidate_scoring_py_is_untouched_by_paper_kelly_simulation() -> None:
    path = Path("courtvision/scoring/candidate_scoring.py")
    content = path.read_text(encoding="utf-8")
    assert "paper_kelly_simulation" not in content
    assert "simulated_fraction" not in content
