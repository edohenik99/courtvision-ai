from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pandas as pd

import courtvision_ai
from courtvision.reporting.quality_summary import write_quality_summary_outputs
from scripts.run_kelly_stakes import main as run_kelly_stakes


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _operator_row(
    *,
    prediction_date: str,
    player_name: str,
    selection: str,
    line: float,
    odds: int,
    edge_pct: float,
    side_edge_pct: float,
    confidence: float,
    context_caution_level: str,
    context_pick_alignment: str,
    team: str,
    opponent: str,
) -> dict[str, object]:
    return {
        "prediction_date": prediction_date,
        "player_name": player_name,
        "entity_name": player_name,
        "team": team,
        "team_abbr": team,
        "opponent": opponent,
        "game_id": f"{prediction_date.replace('-', '')}{team}",
        "market_type": "player_points",
        "selection": selection,
        "line": line,
        "sportsbook_line": line,
        "odds": odds,
        "model_projection": line + 3.0 if selection == "over" else line - 3.0,
        "edge": 3.0 if selection == "over" else -3.0,
        "edge_abs": 3.0,
        "edge_pct": edge_pct,
        "side_edge_pct": side_edge_pct,
        "confidence": confidence,
        "quality_score": 90.0,
        "selection_score": 90.0,
        "is_elite": True,
        "is_live_market": True,
        "synthetic_line": False,
        "qualification_reason": "fixture_live_market",
        "line_source": "fixture_live_market",
        "context_caution_level": context_caution_level,
        "context_pick_alignment": context_pick_alignment,
        "context_preview_applied": True,
    }


def test_operator_fixture_smoke_writes_isolated_boards_and_kelly_metadata(tmp_path: Path) -> None:
    prior_date = "2026-04-29"
    prediction_date = "2026-04-30"
    operator_dir = tmp_path / "runtime" / "operator"
    operator_dir.mkdir(parents=True, exist_ok=True)

    prior_elite_path = operator_dir / f"elite_board_{prior_date}.csv"
    prior_elite_path.write_text("sentinel,elite_board_2026-04-29.csv\n", encoding="utf-8")
    old_time = 1_700_000_000
    os.utime(prior_elite_path, (old_time, old_time))
    prior_mtime_ns = prior_elite_path.stat().st_mtime_ns
    prior_hash = _file_hash(prior_elite_path)

    elite_fixture = pd.DataFrame(
        [
            _operator_row(
                prediction_date=prediction_date,
                player_name="Normal Eligible Under",
                selection="under",
                line=21.5,
                odds=-110,
                edge_pct=-0.12,
                side_edge_pct=0.12,
                confidence=0.80,
                context_caution_level="low",
                context_pick_alignment="aligned",
                team="BOS",
                opponent="NYK",
            ),
            _operator_row(
                prediction_date=prediction_date,
                player_name="High Caution Over",
                selection="over",
                line=17.5,
                odds=-110,
                edge_pct=0.20,
                side_edge_pct=0.20,
                confidence=0.85,
                context_caution_level="high",
                context_pick_alignment="conflicted",
                team="LAL",
                opponent="GSW",
            ),
            _operator_row(
                prediction_date=prediction_date,
                player_name="Medium Neutral Over",
                selection="over",
                line=8.5,
                odds=-110,
                edge_pct=0.50,
                side_edge_pct=0.50,
                confidence=1.00,
                context_caution_level="medium",
                context_pick_alignment="neutral",
                team="DEN",
                opponent="MIN",
            ),
        ]
    )

    paths = courtvision_ai._write_cli_outputs(
        out_dir=tmp_path,
        prediction_date=prediction_date,
        fit_metrics=None,
        prediction_outputs={
            "selected_props": elite_fixture,
            "elite_props": elite_fixture,
            "qualified_pool_props": elite_fixture,
            "full_market_props": elite_fixture,
            "sgp_props": pd.DataFrame(),
            "summary": {
                "prediction_date": prediction_date,
                "pipeline_mode": "fixture_operator_smoke",
            },
            "grading_results": pd.DataFrame(),
        },
        verbose_outputs=False,
    )

    assert paths["elite_board"] == operator_dir / f"elite_board_{prediction_date}.csv"
    assert prior_elite_path.stat().st_mtime_ns == prior_mtime_ns
    assert _file_hash(prior_elite_path) == prior_hash

    elite_board = pd.read_csv(paths["elite_board"])
    assert len(elite_board) == 3
    assert {"context_caution_level", "context_pick_alignment"}.issubset(elite_board.columns)
    assert (operator_dir / f"full_market_board_{prediction_date}.csv").exists()
    assert (operator_dir / f"sgp_board_{prediction_date}.csv").exists()

    kelly_output_path = operator_dir / f"kelly_stakes_{prediction_date}.csv"
    rc = run_kelly_stakes(
        [
            "--prediction-date",
            prediction_date,
            "--bankroll",
            "1000",
            "--input-csv",
            str(paths["elite_board"]),
            "--output-csv",
            str(kelly_output_path),
            "--max-daily-exposure",
            "1.0",
        ]
    )

    assert rc == 0
    kelly_df = pd.read_csv(kelly_output_path)
    expected_columns = {
        "kelly_eligible",
        "context_caution_level",
        "context_pick_alignment",
        "stake_dampener_reason",
        "stake_dampener_factor",
    }
    assert expected_columns.issubset(kelly_df.columns)

    by_player = kelly_df.set_index("player_name")
    normal = by_player.loc["Normal Eligible Under"]
    high_caution = by_player.loc["High Caution Over"]
    dampened = by_player.loc["Medium Neutral Over"]

    assert bool(normal["kelly_eligible"]) is True
    assert normal["stake_amount"] > 0
    assert normal["stake_dampener_factor"] == 1.0
    assert pd.isna(normal["stake_dampener_reason"])

    assert bool(high_caution["kelly_eligible"]) is False
    assert high_caution["skip_reason"] == "context_high_caution_over"
    assert high_caution["stake_amount"] == 0.0
    assert high_caution["stake_dampener_factor"] == 1.0

    assert bool(dampened["kelly_eligible"]) is True
    assert dampened["skip_reason"] == "" or pd.isna(dampened["skip_reason"])
    assert dampened["stake_dampener_reason"] == "medium_neutral_over_dampener"
    assert dampened["stake_dampener_factor"] == 0.5
    assert dampened["stake_amount"] == 10.0

    quality_txt, quality_json, quality_payload = write_quality_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=tmp_path / "runtime",
        out_dir=tmp_path,
        generated_at="2026-05-01T00:00:00+00:00",
    )

    assert quality_txt.exists()
    assert quality_json.exists()
    assert quality_payload["candidate_funnel"]["elite_board_count"] == 3
    assert quality_payload["candidate_funnel"]["kelly_rows_count"] == 3
    assert quality_payload["kelly_safety_summary"]["context_high_caution_over_skip_count"] == 1
    assert quality_payload["kelly_safety_summary"]["medium_neutral_over_dampened_count"] == 1
    assert quality_payload["date_isolation_check"]["status"] == "ok"


def test_empty_elite_board_preserves_full_market_schema(tmp_path: Path) -> None:
    prediction_date = "2026-05-02"
    base_row = _operator_row(
        prediction_date=prediction_date,
        player_name="Schema Anchor",
        selection="under",
        line=18.5,
        odds=-110,
        edge_pct=-0.10,
        side_edge_pct=0.10,
        confidence=0.75,
        context_caution_level="low",
        context_pick_alignment="aligned",
        team="OKC",
        opponent="SAS",
    )
    full_market_fixture = pd.DataFrame([base_row]).assign(extra_diagnostic_flag="x")

    paths = courtvision_ai._write_cli_outputs(
        out_dir=tmp_path,
        prediction_date=prediction_date,
        fit_metrics=None,
        prediction_outputs={
            "selected_props": pd.DataFrame(),
            "elite_props": pd.DataFrame(),
            "qualified_pool_props": full_market_fixture,
            "full_market_props": full_market_fixture,
            "sgp_props": pd.DataFrame(),
            "summary": {"prediction_date": prediction_date, "pipeline_mode": "schema_empty_elite"},
            "grading_results": pd.DataFrame(),
        },
        verbose_outputs=False,
    )

    elite_df = pd.read_csv(paths["elite_board"])
    full_market_df = pd.read_csv(paths["full_market_board"])
    assert elite_df.empty
    assert list(elite_df.columns) == list(full_market_df.columns)
    assert "extra_diagnostic_flag" in elite_df.columns
