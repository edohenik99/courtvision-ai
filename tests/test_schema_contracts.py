from __future__ import annotations

from pathlib import Path

import pandas as pd

import courtvision_ai
from courtvision.reporting.quality_summary import write_quality_summary_outputs
from scripts.run_kelly_stakes import main as run_kelly_stakes
from tests.schema_contracts import (
    BOARD_DIAGNOSTICS_REQUIRED_KEYS,
    ELITE_REQUIRED_COLUMNS,
    FULL_MARKET_REQUIRED_COLUMNS,
    KELLY_REQUIRED_COLUMNS,
    QUALITY_SUMMARY_CANDIDATE_FUNNEL_REQUIRED_KEYS,
    QUALITY_SUMMARY_REQUIRED_KEYS,
)


def _row(prediction_date: str, player_name: str) -> dict[str, object]:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "entity_name": player_name,
        "team": "BOS",
        "team_abbr": "BOS",
        "opponent": "NYK",
        "game_id": f"{prediction_date.replace('-', '')}BOS",
        "market_type": "player_points",
        "selection": "under",
        "line": 21.5,
        "sportsbook_line": 21.5,
        "odds": -110,
        "model_projection": 18.0,
        "edge": -3.5,
        "confidence": 0.80,
        "quality_score": 90.0,
        "selection_score": 90.0,
        "is_elite": True,
        "is_live_market": True,
        "line_source": "fixture_live_market",
        "context_caution_level": "low",
        "context_pick_alignment": "aligned",
    }


def test_runtime_artifact_minimum_contracts_allow_append_only(tmp_path: Path) -> None:
    prediction_date = "2026-05-03"
    full_market = pd.DataFrame([_row(prediction_date, "Schema One")]).assign(extra_diagnostic_flag="kept")

    paths = courtvision_ai._write_cli_outputs(
        out_dir=tmp_path,
        prediction_date=prediction_date,
        fit_metrics=None,
        prediction_outputs={
            "selected_props": full_market,
            "elite_props": full_market,
            "qualified_pool_props": full_market,
            "full_market_props": full_market,
            "sgp_props": pd.DataFrame(),
            "summary": {"prediction_date": prediction_date, "pipeline_mode": "schema_contract"},
            "grading_results": pd.DataFrame(),
        },
        verbose_outputs=False,
    )

    elite_df = pd.read_csv(paths["elite_board"])
    full_df = pd.read_csv(paths["full_market_board"])

    assert FULL_MARKET_REQUIRED_COLUMNS.issubset(set(full_df.columns))
    assert ELITE_REQUIRED_COLUMNS.issubset(set(elite_df.columns))
    assert "extra_diagnostic_flag" in elite_df.columns

    kelly_path = tmp_path / "runtime" / "operator" / f"kelly_stakes_{prediction_date}.csv"
    rc = run_kelly_stakes([
        "--prediction-date", prediction_date,
        "--bankroll", "1000",
        "--input-csv", str(paths["elite_board"]),
        "--output-csv", str(kelly_path),
        "--max-daily-exposure", "1.0",
    ])
    assert rc == 0
    kelly_df = pd.read_csv(kelly_path)
    assert KELLY_REQUIRED_COLUMNS.issubset(set(kelly_df.columns))

    _txt, _json, payload = write_quality_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=tmp_path / "runtime",
        out_dir=tmp_path,
        generated_at="2026-05-03T00:00:00+00:00",
    )
    assert QUALITY_SUMMARY_REQUIRED_KEYS.issubset(set(payload.keys()))
    assert QUALITY_SUMMARY_CANDIDATE_FUNNEL_REQUIRED_KEYS.issubset(set(payload["candidate_funnel"].keys()))

    diagnostics_path = tmp_path / "runtime" / "diagnostics" / f"board_diagnostics_{prediction_date}.json"
    diagnostics = pd.read_json(diagnostics_path, typ="series")
    assert BOARD_DIAGNOSTICS_REQUIRED_KEYS.issubset(set(diagnostics.index))
