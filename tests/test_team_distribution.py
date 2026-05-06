from __future__ import annotations

from pathlib import Path

import pandas as pd

from courtvision.reporting.team_distribution import (
    REPORT_COLUMNS,
    REPORT_TITLE,
    build_team_distribution_report,
    report_row_line,
    write_team_distribution_report,
)


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _full_market_row(
    player_name: str = "Player A",
    *,
    prediction_date: str = "2026-05-06",
    team_abbr: str = "BOS",
    opponent: str = "NYK",
    market_type: str = "player_points",
    selection: str = "over",
    edge: float = 2.5,
    confidence: float = 0.72,
    quality_score: float = 80.0,
    context_pick_alignment: str = "aligned",
    context_caution_level: str = "low",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "team_abbr": team_abbr,
        "opponent": opponent,
        "market_type": market_type,
        "selection": selection,
        "line": 22.5,
        "odds": -110,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "context_pick_alignment": context_pick_alignment,
        "context_caution_level": context_caution_level,
    }


def _paper_row(
    player_name: str = "Player A",
    *,
    prediction_date: str = "2026-05-06",
    team_abbr: str = "BOS",
    simulated_stake: float = 0.002,
    pre_cap_simulated_stake: float | None = None,
    paper_bucket: str = "combo_under_watchlist",
) -> dict:
    pre_cap = pre_cap_simulated_stake if pre_cap_simulated_stake is not None else simulated_stake
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "team_abbr": team_abbr,
        "paper_bucket": paper_bucket,
        "market_type": "player_points_rebounds",
        "selection": "under",
        "line": 28.5,
        "simulated_stake": simulated_stake,
        "pre_cap_simulated_stake": pre_cap,
        "simulated_ev": 0.0001,
    }


def _elite_row(
    player_name: str = "Player A",
    *,
    prediction_date: str = "2026-05-06",
    team_abbr: str = "BOS",
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "team_abbr": team_abbr,
        "market_type": "player_points",
        "selection": "over",
        "line": 22.5,
        "edge": 3.1,
        "confidence": 0.80,
        "quality_score": 85.0,
    }


def _kelly_row(
    player_name: str = "Player A",
    *,
    prediction_date: str = "2026-05-06",
    team_abbr: str = "BOS",
    eligible: bool = True,
) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "team_abbr": team_abbr,
        "market_type": "player_points",
        "selection": "over",
        "eligible": eligible,
        "stake_amount": 10.0,
    }


def _write_source(runtime_root: Path, artifact: str, prediction_date: str, rows: list[dict]) -> None:
    path = runtime_root / "operator" / f"{artifact}_{prediction_date}.csv"
    _write_csv(path, rows)


def test_team_distribution_report_is_generated(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    date = "2026-05-06"
    _write_source(runtime_root, "full_market_board", date, [
        _full_market_row("Player A", prediction_date=date, team_abbr="BOS"),
        _full_market_row("Player B", prediction_date=date, team_abbr="LAL"),
    ])
    _write_source(runtime_root, "paper_kelly_simulation", date, [
        _paper_row("Player A", prediction_date=date, team_abbr="BOS"),
    ])

    text_path, csv_path, report_df, summary = write_team_distribution_report(
        prediction_date=date,
        runtime_root=runtime_root,
    )

    assert text_path == runtime_root / "operator" / f"team_distribution_report_{date}.txt"
    assert csv_path == runtime_root / "operator" / f"team_distribution_report_{date}.csv"
    assert text_path.exists()
    assert csv_path.exists()
    assert REPORT_TITLE in text_path.read_text(encoding="utf-8")
    assert list(pd.read_csv(csv_path, keep_default_na=False).columns) == list(REPORT_COLUMNS)
    assert int(summary["total_teams"]) == 2
    assert int(summary["teams_with_paper"]) == 1


def test_uneven_team_distribution_is_detected(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    date = "2026-05-06"
    bos_rows = [_full_market_row(f"P{i}", prediction_date=date, team_abbr="BOS") for i in range(5)]
    lal_rows = [_full_market_row("P99", prediction_date=date, team_abbr="LAL")]
    _write_source(runtime_root, "full_market_board", date, bos_rows + lal_rows)

    _, _, report_df, summary = write_team_distribution_report(
        prediction_date=date,
        runtime_root=runtime_root,
    )

    assert summary["most_represented_team"] == "BOS"
    assert int(summary["most_represented_count"]) == 5
    bos_row = report_df[report_df["team_abbr"] == "BOS"].iloc[0]
    lal_row = report_df[report_df["team_abbr"] == "LAL"].iloc[0]
    assert int(bos_row["full_market_rows"]) == 5
    assert int(lal_row["full_market_rows"]) == 1


def test_paper_cap_limited_diagnosis(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    date = "2026-05-06"
    _write_source(runtime_root, "full_market_board", date, [
        _full_market_row("Player A", prediction_date=date, team_abbr="MIL"),
    ])
    _write_source(runtime_root, "paper_kelly_simulation", date, [
        _paper_row("Player A", prediction_date=date, team_abbr="MIL",
                   simulated_stake=0.001, pre_cap_simulated_stake=0.004),
    ])

    _, _, report_df, summary = write_team_distribution_report(
        prediction_date=date,
        runtime_root=runtime_root,
    )

    mil_row = report_df[report_df["team_abbr"] == "MIL"].iloc[0]
    assert mil_row["diagnosis"] == "paper_cap_limited"
    assert float(mil_row["pre_cap_paper_exposure"]) == 0.004
    assert float(mil_row["post_cap_paper_exposure"]) == 0.001
    assert float(mil_row["exposure_reduced_by_caps"]) > 0
    assert int(summary["teams_cap_limited"]) == 1


def test_context_rejection_driven_diagnosis(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    date = "2026-05-06"
    _write_source(runtime_root, "full_market_board", date, [
        _full_market_row("Player A", prediction_date=date, team_abbr="GSW",
                         context_pick_alignment="conflicted"),
        _full_market_row("Player B", prediction_date=date, team_abbr="GSW",
                         context_pick_alignment="conflicted"),
        _full_market_row("Player C", prediction_date=date, team_abbr="GSW",
                         context_pick_alignment="conflicted"),
    ])

    _, _, report_df, summary = write_team_distribution_report(
        prediction_date=date,
        runtime_root=runtime_root,
    )

    gsw_row = report_df[report_df["team_abbr"] == "GSW"].iloc[0]
    assert gsw_row["diagnosis"] == "context_rejection_driven"
    assert int(gsw_row["context_conflicted_rows"]) == 3
    assert int(summary["teams_context_rejected"]) == 1


def test_real_kelly_file_unchanged_by_team_distribution(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    date = "2026-05-06"
    operator = runtime_root / "operator"
    kelly_path = operator / f"kelly_stakes_{date}.csv"
    _write_csv(kelly_path, [{"player_name": "Kelly Sentinel", "stake_amount": 10.0, "eligible": True}])
    _write_source(runtime_root, "full_market_board", date, [
        _full_market_row("Player A", prediction_date=date, team_abbr="BOS"),
    ])
    before = kelly_path.read_bytes()

    write_team_distribution_report(prediction_date=date, runtime_root=runtime_root)

    assert kelly_path.read_bytes() == before


def test_candidate_scoring_py_untouched_by_team_distribution() -> None:
    path = Path(__file__).parent.parent / "courtvision" / "scoring" / "candidate_scoring.py"
    content = path.read_text(encoding="utf-8")
    assert "team_distribution" not in content
    assert "team_distribution_report" not in content
