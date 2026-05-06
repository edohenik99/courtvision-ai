from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.combo_under_watchlist import (
    COMBO_UNDER_MARKETS,
    OBSERVATION_ONLY_NOTE,
    WATCHLIST_COLUMNS,
    build_combo_under_watchlist,
    write_combo_under_watchlist,
)
from scripts.write_daily_summary import write_daily_summary_outputs


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _combo_under_row(
    player_name: str,
    *,
    market_type: str = "player_points_rebounds_assists",
    selection: str = "under",
    edge: float = -2.0,
    alignment: str = "aligned",
    caution: str = "low",
) -> dict:
    return {
        "prediction_date": "2026-05-06",
        "player_name": player_name,
        "team_abbr": "BOS",
        "opponent": "PHI",
        "market_type": market_type,
        "selection": selection,
        "line": 36.5,
        "model_projection": 33.8,
        "edge": edge,
        "confidence": 0.68,
        "quality_score": 82.0,
        "selection_score": 76.0,
        "odds": -110,
        "context_pick_alignment": alignment,
        "context_caution_level": caution,
        "context_conflict_cause": "",
        "final_elite_rejection_reason": "market_not_elite_eligible",
        "result_status": "pending",
        "actual_value": "",
    }


def test_combo_under_watchlist_is_generated(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    _write_csv(
        runtime_root / "operator" / f"full_market_board_{prediction_date}.csv",
        [_combo_under_row("Combo Under")],
    )

    output_path, watchlist = write_combo_under_watchlist(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    assert output_path == runtime_root / "operator" / f"combo_under_watchlist_{prediction_date}.csv"
    assert output_path.exists()
    assert watchlist["player_name"].tolist() == ["Combo Under"]
    assert list(pd.read_csv(output_path).columns) == list(WATCHLIST_COLUMNS)


def test_empty_combo_under_watchlist_still_writes_headers(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    _write_csv(
        runtime_root / "operator" / f"full_market_board_{prediction_date}.csv",
        [_combo_under_row("Points Only", market_type="player_points")],
        columns=list(WATCHLIST_COLUMNS),
    )

    output_path, watchlist = write_combo_under_watchlist(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    written = pd.read_csv(output_path)
    assert watchlist.empty
    assert written.empty
    assert list(written.columns) == list(WATCHLIST_COLUMNS)


def test_combo_under_watchlist_filters_only_aligned_low_combo_under_rows() -> None:
    rows = [
        _combo_under_row("Points Assists", market_type="player_points_assists"),
        _combo_under_row("Points Rebounds", market_type="player_points_rebounds"),
        _combo_under_row("PRA", market_type="player_points_rebounds_assists"),
        _combo_under_row("Over Combo", selection="over"),
        _combo_under_row("Medium Caution", caution="medium"),
        _combo_under_row("Neutral Alignment", alignment="neutral"),
        _combo_under_row("Points Market", market_type="player_points"),
    ]

    watchlist = build_combo_under_watchlist(pd.DataFrame(rows))

    assert watchlist["player_name"].tolist() == ["Points Assists", "Points Rebounds", "PRA"]
    assert set(watchlist["market_type"]) == set(COMBO_UNDER_MARKETS)
    assert set(watchlist["selection"].str.lower()) == {"under"}
    assert set(watchlist["context_pick_alignment"].str.lower()) == {"aligned"}
    assert set(watchlist["context_caution_level"].str.lower()) == {"low"}


def test_combo_under_watchlist_sorts_by_absolute_edge_descending() -> None:
    rows = [
        _combo_under_row("Small", edge=-1.0),
        _combo_under_row("Largest Negative", edge=-4.0),
        _combo_under_row("Large Positive", edge=3.5),
    ]

    watchlist = build_combo_under_watchlist(pd.DataFrame(rows))

    assert watchlist["player_name"].tolist() == ["Largest Negative", "Large Positive", "Small"]


def test_daily_summary_includes_combo_under_observation_only_section(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=["prediction_date", "player_name"])
    _write_csv(
        operator / f"kelly_stakes_{prediction_date}.csv",
        [],
        columns=["eligible", "stake_amount", "expected_value"],
    )
    _write_csv(
        operator / f"full_market_board_{prediction_date}.csv",
        [
            _combo_under_row("Lower Edge", edge=-1.25),
            _combo_under_row("Top Edge", edge=-4.50),
        ],
    )
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / f"market_shadow_grading_{prediction_date}.json").write_text(
        json.dumps({"kelly_decision_performance": {"by_kelly_eligible": {"true": {}, "false": {}}}}),
        encoding="utf-8",
    )

    output_path, metadata = write_daily_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert "Combo UNDER Promotion Watchlist — Observation Only / No Kelly" in text
    assert "- watchlist row count: 2" in text
    assert OBSERVATION_ONLY_NOTE in text
    assert "not Elite/Kelly eligible yet" in text
    assert text.index("Top Edge") < text.index("Lower Edge")
    assert metadata["combo_under_watchlist_count"] == 2
    assert (operator / f"combo_under_watchlist_{prediction_date}.csv").exists()


def test_combo_under_watchlist_does_not_modify_kelly_output_or_candidate_scoring(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    kelly_path = operator / f"kelly_stakes_{prediction_date}.csv"
    candidate_scoring_path = Path("courtvision/scoring/candidate_scoring.py")
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [_combo_under_row("Combo Under")])
    _write_csv(
        kelly_path,
        [{"player_name": "Kelly Sentinel", "market_type": "player_points", "eligible": True, "stake_amount": 10.0}],
    )
    kelly_before = kelly_path.read_bytes()
    candidate_scoring_before = candidate_scoring_path.read_bytes()

    write_combo_under_watchlist(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    assert kelly_path.read_bytes() == kelly_before
    assert candidate_scoring_path.read_bytes() == candidate_scoring_before
    kelly = pd.read_csv(kelly_path)
    assert set(kelly["market_type"]) == {"player_points"}
