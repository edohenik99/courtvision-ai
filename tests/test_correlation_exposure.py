from __future__ import annotations

from pathlib import Path

import pandas as pd

from courtvision.reporting.correlation_exposure import (
    REPORT_COLUMNS,
    REPORT_TITLE,
    build_correlation_exposure_report,
    write_correlation_exposure_report,
)
from scripts.write_daily_summary import write_daily_summary_outputs


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _candidate_row(
    player_name: str,
    *,
    prediction_date: str = "2026-05-06",
    team_abbr: str = "BOS",
    opponent: str = "NYK",
    market_type: str = "player_points",
    selection: str = "over",
    line: float = 20.5,
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
        "line": line,
        "odds": -110,
        "edge": 2.0,
        "confidence": 0.75,
        "quality_score": 82.0,
        "context_pick_alignment": context_pick_alignment,
        "context_caution_level": context_caution_level,
    }


def _kelly_row(player_name: str = "Kelly Sentinel", *, prediction_date: str = "2026-05-06") -> dict:
    return {
        **_candidate_row(player_name, prediction_date=prediction_date, selection="under", line=18.5),
        "eligible": True,
        "stake_amount": 10.0,
        "expected_value": 1.2,
    }


def _paper_row(player_name: str = "Paper Sentinel", *, prediction_date: str = "2026-05-06") -> dict:
    return {
        **_candidate_row(
            player_name,
            prediction_date=prediction_date,
            market_type="player_points_rebounds",
            selection="under",
            line=28.5,
        ),
        "paper_bucket": "combo_under_watchlist",
        "directional_edge": 2.0,
        "simulated_fraction": 0.001,
        "simulated_stake": 0.001,
        "simulated_ev": 0.0001,
        "real_kelly_eligible": False,
        "simulation_only": True,
        "reason_not_real_kelly": "combo_market_not_real_kelly_eligible",
    }


def test_correlation_exposure_report_is_generated(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    operator = runtime_root / "operator"
    date = "2026-05-06"
    _write_csv(operator / f"elite_board_{date}.csv", [_candidate_row("Elite Sentinel", prediction_date=date)])
    _write_csv(operator / f"kelly_stakes_{date}.csv", [_kelly_row(prediction_date=date)])
    _write_csv(operator / f"paper_kelly_simulation_{date}.csv", [_paper_row(prediction_date=date)])
    _write_csv(operator / f"high_caution_over_watchlist_{date}.csv", [_candidate_row("Watch Over", prediction_date=date)])
    _write_csv(
        operator / f"combo_under_watchlist_{date}.csv",
        [_candidate_row("Watch Under", prediction_date=date, market_type="player_points_rebounds", selection="under")],
    )
    _write_csv(operator / f"full_market_board_{date}.csv", [_candidate_row("Full Candidate", prediction_date=date)])

    text_path, csv_path, report, summary = write_correlation_exposure_report(
        prediction_date=date,
        runtime_root=runtime_root,
    )

    assert text_path == operator / f"correlation_exposure_report_{date}.txt"
    assert csv_path == operator / f"correlation_exposure_report_{date}.csv"
    assert text_path.exists()
    assert csv_path.exists()
    assert REPORT_TITLE in text_path.read_text(encoding="utf-8")
    assert list(pd.read_csv(csv_path, keep_default_na=False).columns) == list(REPORT_COLUMNS)
    assert summary["total_rows"] == 6
    assert not report.empty


def test_repeated_player_exposure_is_detected(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    operator = runtime_root / "operator"
    date = "2026-05-06"
    _write_csv(
        operator / f"full_market_board_{date}.csv",
        [
            _candidate_row("Repeat Player", prediction_date=date, market_type="player_points"),
            _candidate_row("Repeat Player", prediction_date=date, market_type="player_assists"),
            _candidate_row("Other Player", prediction_date=date),
        ],
    )
    report, summary = build_correlation_exposure_report(prediction_date=date, runtime_root=runtime_root)

    player_rows = report[(report["risk_type"] == "player") & (report["group_key"] == "Repeat Player")]
    assert summary["repeated_player_count"] == 1
    assert summary["max_rows_per_player"] == 2
    assert len(player_rows) == 1
    assert player_rows.iloc[0]["risk_label"] == "medium"


def test_same_game_concentration_is_detected(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    operator = runtime_root / "operator"
    date = "2026-05-06"
    _write_csv(
        operator / f"full_market_board_{date}.csv",
        [
            _candidate_row("Player A", prediction_date=date, team_abbr="BOS", opponent="NYK"),
            _candidate_row("Player B", prediction_date=date, team_abbr="NYK", opponent="BOS"),
            _candidate_row("Player C", prediction_date=date, team_abbr="BOS", opponent="NYK", market_type="player_assists"),
        ],
    )

    report, summary = build_correlation_exposure_report(prediction_date=date, runtime_root=runtime_root)

    game_rows = report[(report["risk_type"] == "game") & (report["group_key"] == "BOS-NYK")]
    assert summary["max_rows_per_game"] == 3
    assert len(game_rows) == 1
    assert int(game_rows.iloc[0]["total_rows"]) == 3


def test_paper_kelly_rows_are_included(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    date = "2026-05-06"
    _write_csv(runtime_root / "operator" / f"paper_kelly_simulation_{date}.csv", [_paper_row(prediction_date=date)])

    report, summary = build_correlation_exposure_report(prediction_date=date, runtime_root=runtime_root)

    assert summary["paper_kelly_rows"] == 1
    assert "combo_under_watchlist" in set(report["source_buckets"].astype(str))
    assert "paper_kelly_simulation" in set(report["source_artifacts"].astype(str))


def test_daily_summary_includes_correlation_exposure_section(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    history_root = tmp_path / "data" / "history"
    operator = runtime_root / "operator"
    date = "2026-05-06"
    _write_csv(operator / f"elite_board_{date}.csv", [_candidate_row("Elite Sentinel", prediction_date=date)])
    _write_csv(operator / f"kelly_stakes_{date}.csv", [_kelly_row(prediction_date=date)])
    _write_csv(
        operator / f"full_market_board_{date}.csv",
        [
            _candidate_row("Repeat Player", prediction_date=date, market_type="player_points"),
            _candidate_row("Repeat Player", prediction_date=date, market_type="player_assists"),
        ],
    )

    output_path, metadata = write_daily_summary_outputs(
        prediction_date=date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert REPORT_TITLE in text
    assert Path(metadata["correlation_exposure_report_path"]).exists()
    assert Path(metadata["correlation_exposure_report_csv_path"]).exists()
    assert metadata["correlation_exposure_summary"]["repeated_player_count"] >= 1


def test_real_kelly_file_unchanged_by_correlation_report(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    operator = runtime_root / "operator"
    date = "2026-05-06"
    _write_csv(operator / f"elite_board_{date}.csv", [_candidate_row("Elite Sentinel", prediction_date=date)])
    kelly_path = operator / f"kelly_stakes_{date}.csv"
    _write_csv(kelly_path, [_kelly_row(prediction_date=date)])
    before = kelly_path.read_bytes()

    write_correlation_exposure_report(prediction_date=date, runtime_root=runtime_root)

    assert kelly_path.read_bytes() == before


def test_candidate_scoring_py_is_untouched_by_correlation_report() -> None:
    path = Path(__file__).parent.parent / "courtvision" / "scoring" / "candidate_scoring.py"
    content = path.read_text(encoding="utf-8")
    assert "correlation_exposure" not in content
    assert "Correlation Exposure" not in content
