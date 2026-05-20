from __future__ import annotations

import pandas as pd

from courtvision.reason_codes import (
    DUPLICATE_BETTING_IDENTITY_REASON,
    SELECTION_LIVE_GATE_FILTERED_REASON,
    SELECTION_LIVE_GATE_MISSING_QUALIFICATION_REASON,
    SELECTION_NOT_LIVE_MARKET_ELIGIBLE_REASON,
    UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON,
    UNSUPPORTED_MILESTONE_MARKET_REASON,
)
from courtvision.selection.operator_boards import build_operator_boards


def _candidate(label: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "prediction_date": "2026-05-20",
        "player_name": label,
        "entity_name": label,
        "player_id": label,
        "team": "AAA",
        "team_abbr": "AAA",
        "game_id": f"game-{label}",
        "market_type": "player_points",
        "selection": "over",
        "line": 20.5,
        "sportsbook_line": 20.5,
        "odds": -110,
        "edge": 3.0,
        "edge_pct": 0.12,
        "confidence": 0.82,
        "quality_score": 80.0,
        "selection_score": 80.0,
        "is_elite": True,
        "is_live_market": True,
        "synthetic_line": False,
        "line_source": "live_market",
        "qualification_reason": "live_market_qualified",
        "source_lane": "live_market_candidate",
        "vendor": "draftkings",
        "bookmaker": "draftkings",
    }
    row.update(overrides)
    return row


def _select_all(df: pd.DataFrame) -> pd.DataFrame:
    return df.copy()


def _reason_counts(trace: dict[str, object]) -> dict[str, int]:
    rows = trace.get("selection_rejection_reasons", [])
    assert isinstance(rows, list)
    return {str(row["reason"]): int(row["count"]) for row in rows}


def test_live_source_synthetic_gate_characterization_matrix() -> None:
    missing_live_flag = _candidate("missing-live-flag", player_id="missing-live-flag")
    missing_live_flag.pop("is_live_market")
    rows = [
        _candidate(
            "live-via-line-source",
            player_id="line-source",
            qualification_reason="",
            source_lane="",
        ),
        _candidate(
            "live-via-sportsbook-qualification",
            player_id="sportsbook-qualification",
            line_source="provider_feed",
            qualification_reason="sportsbook_edge_pass",
            source_lane="",
        ),
        _candidate(
            "source-lane-only-empty-qualification",
            player_id="source-lane-empty",
            line_source="",
            qualification_reason="",
            source_lane="live_market_candidate",
            vendor="fanduel",
            bookmaker="draftkings",
        ),
        _candidate(
            "source-lane-only-nonlive-qualification",
            player_id="source-lane-filtered",
            line_source="",
            qualification_reason="model_pass",
            source_lane="live_market_candidate",
            vendor="fanduel",
            bookmaker="draftkings",
        ),
        _candidate(
            "synthetic-live-source",
            player_id="synthetic",
            synthetic_line=True,
            line_source="live_market",
            qualification_reason="live_market_qualified",
        ),
        missing_live_flag,
        _candidate(
            "unsupported-active-market",
            player_id="unsupported-active",
            market_type="player_blocks",
        ),
        _candidate(
            "unsupported-milestone",
            player_id="milestone",
            raw_market_type="milestone",
        ),
    ]

    elite_df, full_market_df, trace = build_operator_boards(
        pd.DataFrame(rows),
        select_elite_board=_select_all,
        select_top_per_market=lambda df, limit: df.copy(),
    )

    assert list(elite_df["player_name"]) == [
        "live-via-line-source",
        "live-via-sportsbook-qualification",
    ]
    assert list(full_market_df["player_name"]) == [
        "live-via-line-source",
        "live-via-sportsbook-qualification",
    ]
    assert trace["elite"]["input_count"] == 8
    assert trace["elite"]["post_live_market_gate_count"] == 2
    assert trace["elite"]["post_duplicate_betting_identity_dedupe_count"] == 2
    assert trace["elite"]["selected_count"] == 2
    assert trace["full_market"]["selected_count"] == 2
    assert trace["elite"]["diagnostic_live_flag_count"] == 6
    assert trace["elite"]["unsupported_active_operator_market_count"] == 1
    assert trace["elite"]["unsupported_active_operator_market_counts"] == {
        "player_blocks": 1,
    }
    assert trace["elite"]["unsupported_milestone_count"] == 1

    assert _reason_counts(trace) == {
        SELECTION_LIVE_GATE_MISSING_QUALIFICATION_REASON: 1,
        SELECTION_LIVE_GATE_FILTERED_REASON: 1,
        SELECTION_NOT_LIVE_MARKET_ELIGIBLE_REASON: 2,
        UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON: 1,
        UNSUPPORTED_MILESTONE_MARKET_REASON: 1,
    }


def test_duplicate_betting_identity_runs_after_live_gate_characterization() -> None:
    lower_live_duplicate = _candidate(
        "live-duplicate-lower",
        player_id=42,
        game_id="game-42",
        market_type="player_points",
        selection="over",
        line=13.5,
        sportsbook_line=13.5,
        selection_score=20.0,
        quality_score=90.0,
    )
    higher_live_duplicate = _candidate(
        "live-duplicate-higher",
        player_id=42,
        game_id="game-42",
        market_type="player_points",
        selection="over",
        line=13.5,
        sportsbook_line=13.5,
        selection_score=21.0,
        quality_score=40.0,
    )
    non_live_same_identity = _candidate(
        "non-live-same-identity",
        player_id=42,
        game_id="game-42",
        market_type="player_points",
        selection="over",
        line=13.5,
        sportsbook_line=13.5,
        is_live_market=False,
        selection_score=99.0,
        quality_score=99.0,
    )

    elite_df, full_market_df, trace = build_operator_boards(
        pd.DataFrame([lower_live_duplicate, higher_live_duplicate, non_live_same_identity]),
        select_elite_board=_select_all,
        select_top_per_market=lambda df, limit: df.copy(),
    )

    assert list(elite_df["player_name"]) == ["live-duplicate-higher"]
    assert list(full_market_df["player_name"]) == ["live-duplicate-higher"]
    assert trace["elite"]["post_live_market_gate_count"] == 2
    assert trace["elite"]["post_duplicate_betting_identity_dedupe_count"] == 1
    assert trace["elite"]["duplicate_betting_identity_drop_count"] == 1
    assert trace["elite"]["duplicate_betting_identity_drop_counts_by_market_type"] == {
        "player_points": 1,
    }
    assert _reason_counts(trace) == {
        SELECTION_NOT_LIVE_MARKET_ELIGIBLE_REASON: 1,
        DUPLICATE_BETTING_IDENTITY_REASON: 1,
    }

