from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.shadow_candidate_lane import (
    COMBO_OVER_WEAK_POSITIVE_RESEARCH,
    HIGH_CAUTION_OVER_DO_NOT_PROMOTE,
    INCUBATOR_RESEARCH,
    NEAR_ELITE_RESEARCH,
    UNDER_ALIGNED_RESEARCH,
    build_shadow_candidate_lane,
    resolve_source_artifact_date,
    write_shadow_candidate_lane_outputs,
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _full_row(
    player: str,
    *,
    player_id: str | None = None,
    market_type: str = "player_points",
    selection: str = "under",
    context_caution_level: str = "low",
    context_pick_alignment: str = "supports_under",
    source_rejection_reason: str = "unknown",
    line: float = 20.5,
    edge: float = -2.5,
    confidence: float = 0.74,
    quality_score: float = 64.0,
) -> dict:
    return {
        "prediction_date": "2026-05-02",
        "player_id": player_id or player.lower().replace(" ", "-"),
        "player_name": player,
        "team_abbr": "OKC",
        "opponent": "SAS",
        "game_id": "game-1",
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "sportsbook_line": line,
        "odds": -110,
        "model_projection": line + edge,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "selection_score": 55.0,
        "context_caution_level": context_caution_level,
        "context_pick_alignment": context_pick_alignment,
        "final_elite_rejection_reason": source_rejection_reason,
        "kelly_eligible": False,
    }


def _safe_action_payload() -> dict:
    return {
        "report_name": "safe_action_discovery_report",
        "recommendation_matrix": [
            {
                "bucket_scope": "selection",
                "bucket_key": "selection=under",
                "history_source": "all",
                "market_type": "all",
                "selection": "under",
                "context_caution_level": "all",
                "context_edge_label": "all",
                "source_rejection_reason": "all",
                "graded_rows": 278,
                "hit_rate": 0.6187,
                "roi": 0.1135,
                "clv_coverage_rate": 0.0,
                "recommendation": "SHADOW_ONLY",
            },
            {
                "bucket_scope": "source_reason",
                "bucket_key": "combo_over",
                "history_source": "market_shadow_history",
                "market_type": "player_points_rebounds_assists",
                "selection": "over",
                "context_caution_level": "high",
                "context_edge_label": "conflicted",
                "source_rejection_reason": "elite_reject_context_high_caution_over",
                "graded_rows": 21,
                "hit_rate": 0.6667,
                "roi": 0.2297,
                "clv_coverage_rate": 0.0,
                "recommendation": "SHADOW_ONLY",
            },
            {
                "bucket_scope": "source_reason",
                "bucket_key": "points_over_unsafe",
                "history_source": "market_shadow_history",
                "market_type": "player_points",
                "selection": "over",
                "context_caution_level": "high",
                "context_edge_label": "conflicted",
                "source_rejection_reason": "elite_reject_context_high_caution_over",
                "graded_rows": 99,
                "hit_rate": 0.4242,
                "roi": -0.2113,
                "clv_coverage_rate": 0.0,
                "recommendation": "KEEP_BLOCKED",
            },
        ],
    }


def test_builds_shadow_lanes_excludes_full_market_obvious_unsafe_and_forces_flags(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    prediction_date = "2026-05-02"

    under_row = _full_row("Under Candidate")
    full_rows = [
        under_row,
        under_row.copy(),  # duplicate should be removed
        _full_row(
            "Combo Over Candidate",
            market_type="player_points_rebounds_assists",
            selection="over",
            context_caution_level="high",
            context_pick_alignment="conflicted",
            source_rejection_reason="elite_reject_context_high_caution_over",
            line=34.5,
            edge=3.5,
            confidence=0.76,
            quality_score=70.0,
        ),
        _full_row(
            "Unsafe Points Over",
            market_type="player_points",
            selection="over",
            context_caution_level="high",
            context_pick_alignment="conflicted",
            source_rejection_reason="elite_reject_context_high_caution_over",
            line=13.5,
            edge=7.0,
            confidence=0.8,
            quality_score=76.0,
        ),
        _full_row(
            "High Caution Blocks",
            market_type="player_blocks",
            selection="over",
            context_caution_level="high",
            context_pick_alignment="conflicted",
            source_rejection_reason="elite_reject_context_high_caution_over",
            line=1.5,
            edge=1.1,
            confidence=0.7,
            quality_score=58.0,
        ),
    ]
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", full_rows)
    _write_csv(
        operator / f"near_elite_review_{prediction_date}.csv",
        [
            _full_row(
                "Near Elite Candidate",
                market_type="player_points",
                selection="over",
                context_caution_level="high",
                context_pick_alignment="conflicted",
                source_rejection_reason="elite_reject_context_high_caution_over",
                line=12.5,
                edge=4.0,
                confidence=0.72,
                quality_score=62.0,
            )
            | {"review_lane": "near_elite", "operator_action": "REVIEW_ONLY"}
        ],
    )
    _write_csv(
        operator / f"incubator_board_{prediction_date}.csv",
        [
            {
                "prediction_date": prediction_date,
                "player": "Incubator Candidate",
                "player_id": "incubator-candidate",
                "team": "OKC",
                "opponent": "SAS",
                "market_type": "player_points",
                "selection": "over",
                "line": 13.5,
                "odds": -112,
                "edge": 6.0,
                "confidence": 0.8,
                "quality_score": 76.0,
                "context_alignment": "conflicted",
                "context_caution_level": "high",
                "source_rejection_reason": "elite_reject_context_high_caution_over",
                "real_money_eligible": False,
            }
        ],
    )
    _write_json(diagnostics / f"safe_action_discovery_report_{prediction_date}.json", _safe_action_payload())

    payload, board_df = build_shadow_candidate_lane(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        generated_at_utc="2026-05-02T00:00:00+00:00",
    )

    assert payload["summary"]["excluded_obvious_unsafe_full_market_rows"] == 1
    assert payload["summary"]["deduplicated_rows_removed"] == 1
    assert payload["summary"]["shadow_candidate_count"] == 5
    assert set(board_df["research_lane"]) == {
        UNDER_ALIGNED_RESEARCH,
        COMBO_OVER_WEAK_POSITIVE_RESEARCH,
        NEAR_ELITE_RESEARCH,
        INCUBATOR_RESEARCH,
        HIGH_CAUTION_OVER_DO_NOT_PROMOTE,
    }
    assert "Unsafe Points Over" not in set(board_df["player_name"])
    assert (~board_df["real_money_eligible"].astype(bool)).all()
    assert (~board_df["kelly_eligible"].astype(bool)).all()
    assert (~board_df["elite_eligible"].astype(bool)).all()
    assert board_df["shadow_only"].astype(bool).all()
    assert payload["all_rows_real_money_eligible_false"] is True


def test_source_date_falls_back_to_latest_available_board(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_csv(runtime_root / "operator" / "full_market_board_2026-05-01.csv", [_full_row("Old Candidate")])

    assert (
        resolve_source_artifact_date(prediction_date="2026-05-03", runtime_root=runtime_root)
        == "2026-05-01"
    )


def test_write_outputs_does_not_touch_pick_history(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    prediction_date = "2026-05-02"
    _write_csv(runtime_root / "operator" / f"full_market_board_{prediction_date}.csv", [_full_row("Under Candidate")])
    _write_json(runtime_root / "diagnostics" / f"safe_action_discovery_report_{prediction_date}.json", _safe_action_payload())
    pick_history = history_root / "pick_history.csv"
    _write_csv(
        pick_history,
        [
            {
                "prediction_date": "2026-05-01",
                "player_name": "Historical Pick",
                "market": "player_points",
                "selection": "under",
                "result_status": "hit",
            }
        ],
    )
    original_pick_history = pick_history.read_text(encoding="utf-8")

    board_path, text_path, json_path, payload = write_shadow_candidate_lane_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert board_path.exists()
    assert text_path.exists()
    assert json_path.exists()
    assert payload["betting_logic_changed"] is False
    assert payload["real_money_promotion_recommended"] is False
    assert pick_history.read_text(encoding="utf-8") == original_pick_history


def test_empty_sources_write_empty_shadow_board(tmp_path: Path) -> None:
    payload, board_df = build_shadow_candidate_lane(
        prediction_date="2026-05-02",
        runtime_root=tmp_path / "runtime",
        generated_at_utc="2026-05-02T00:00:00+00:00",
    )

    assert payload["summary"]["shadow_candidate_count"] == 0
    assert board_df.empty
    assert payload["all_rows_real_money_eligible_false"] is True
