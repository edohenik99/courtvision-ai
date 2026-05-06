from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.high_caution_over_watchlist import (
    FINAL_ELITE_REJECTION_REASON,
    KELLY_PROJECTED_SKIP_REASON,
    OBSERVATION_ONLY_NOTE,
    WATCHLIST_COLUMNS,
    build_high_caution_over_watchlist,
    write_high_caution_over_watchlist,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs
from scripts.write_daily_summary import write_daily_summary_outputs


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _watchlist_row(
    player_name: str,
    *,
    edge: str = "1.0",
    kelly_reason: str = "",
    elite_reason: str = "",
) -> dict:
    return {
        "prediction_date": "2026-05-06",
        "player_name": player_name,
        "team_abbr": "PHI",
        "opponent": "BOS",
        "market_type": "player_points",
        "selection": "over",
        "line": 12.5,
        "model_projection": 15.0,
        "edge": edge,
        "confidence": 0.75,
        "quality_score": 88.0,
        "selection_score": 91.0,
        "odds": -110,
        "context_pick_alignment": "conflicted",
        "context_caution_level": "high",
        "context_conflict_cause": "defense_driven",
        "kelly_projected_skip_reason": kelly_reason,
        "final_elite_rejection_reason": elite_reason,
    }


def test_watchlist_csv_is_generated_from_full_market_rows_and_sorted_by_edge(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    full_market_path = runtime_root / "operator" / f"full_market_board_{prediction_date}.csv"
    _write_csv(
        full_market_path,
        [
            _watchlist_row("Low Edge", edge="1.25", kelly_reason=KELLY_PROJECTED_SKIP_REASON),
            _watchlist_row("High Edge", edge="3.50", elite_reason=FINAL_ELITE_REJECTION_REASON),
            _watchlist_row("High Caution But No Reason", edge="9.99"),
            _watchlist_row("Other Rejection", edge="8.88", elite_reason="market_filtered_by_elite_policy"),
        ],
    )

    output_path, watchlist = write_high_caution_over_watchlist(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    assert output_path == runtime_root / "operator" / f"high_caution_over_watchlist_{prediction_date}.csv"
    assert output_path.exists()
    assert watchlist["player_name"].tolist() == ["High Edge", "Low Edge"]
    assert list(pd.read_csv(output_path).columns) == list(WATCHLIST_COLUMNS)


def test_empty_watchlist_still_writes_headers(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    _write_csv(
        runtime_root / "operator" / f"full_market_board_{prediction_date}.csv",
        [_watchlist_row("Safe Under", edge="0.20")],
        columns=list(WATCHLIST_COLUMNS),
    )

    output_path, watchlist = write_high_caution_over_watchlist(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    written = pd.read_csv(output_path)
    assert watchlist.empty
    assert written.empty
    assert list(written.columns) == list(WATCHLIST_COLUMNS)


def test_watchlist_filters_only_explicit_watchlist_reasons() -> None:
    rows = [
        _watchlist_row("Kelly Reason", kelly_reason=KELLY_PROJECTED_SKIP_REASON),
        _watchlist_row("Final Elite Reason", elite_reason=FINAL_ELITE_REJECTION_REASON),
        {
            **_watchlist_row("Legacy Elite Alias"),
            "elite_rejection_reason": FINAL_ELITE_REJECTION_REASON,
        },
        {
            **_watchlist_row("Context Only"),
            "context_pick_alignment": "conflicted",
            "context_caution_level": "high",
        },
    ]

    watchlist = build_high_caution_over_watchlist(pd.DataFrame(rows))

    assert watchlist["player_name"].tolist() == ["Kelly Reason", "Final Elite Reason"]


def test_watchlist_writer_does_not_modify_elite_board_kelly_output_or_candidate_scoring(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    elite_path = operator / f"elite_board_{prediction_date}.csv"
    kelly_path = operator / f"kelly_stakes_{prediction_date}.csv"
    candidate_scoring_path = Path("courtvision/scoring/candidate_scoring.py")

    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [_watchlist_row("Blocked")])
    _write_csv(elite_path, [{"player_name": "Elite Sentinel", "selection": "under"}])
    _write_csv(kelly_path, [{"player_name": "Kelly Sentinel", "eligible": True, "stake_amount": 10.0}])
    elite_before = elite_path.read_bytes()
    kelly_before = kelly_path.read_bytes()
    candidate_scoring_before = candidate_scoring_path.read_bytes()

    write_high_caution_over_watchlist(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    assert elite_path.read_bytes() == elite_before
    assert kelly_path.read_bytes() == kelly_before
    assert candidate_scoring_path.read_bytes() == candidate_scoring_before


def test_daily_summary_includes_observation_only_watchlist_section(tmp_path: Path) -> None:
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
            _watchlist_row("Low Edge", edge="1.25", kelly_reason=KELLY_PROJECTED_SKIP_REASON),
            _watchlist_row("High Edge", edge="3.50", elite_reason=FINAL_ELITE_REJECTION_REASON),
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
    assert "High-Caution OVER Watchlist — Observation Only / No Stake" in text
    assert "- watchlist row count: 2" in text
    assert OBSERVATION_ONLY_NOTE in text
    assert text.index("High Edge") < text.index("Low Edge")
    assert metadata["high_caution_over_watchlist_count"] == 2
    assert (operator / f"high_caution_over_watchlist_{prediction_date}.csv").exists()


def test_quality_summary_references_high_caution_over_watchlist_artifact(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    _write_csv(
        operator / f"elite_board_{prediction_date}.csv",
        [_watchlist_row("Safe Under", edge="0.25") | {"selection": "under"}],
    )
    _write_csv(
        operator / f"kelly_stakes_{prediction_date}.csv",
        [{"player_name": "Safe Under", "kelly_eligible": True, "stake_amount": 8.0, "expected_value": 1.1}],
    )
    _write_csv(
        operator / f"full_market_board_{prediction_date}.csv",
        [_watchlist_row("Blocked", edge="2.00", kelly_reason=KELLY_PROJECTED_SKIP_REASON)],
    )

    text_path, _, payload = write_quality_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-06T00:00:00+00:00",
    )

    watchlist = payload["high_caution_over_watchlist"]
    assert watchlist["row_count"] == 1
    assert watchlist["observation_only"] is True
    assert Path(watchlist["path"]).exists()
    assert str(Path(watchlist["path"])) in payload["date_isolation_check"]["prediction_artifacts"]
    text = text_path.read_text(encoding="utf-8")
    assert "High-Caution OVER Watchlist" in text
    assert watchlist["path"] in text
