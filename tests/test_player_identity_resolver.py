from __future__ import annotations

import pandas as pd

from courtvision.context.game_context import (
    IDENTITY_OUTSIDE_TEAM_REASON,
    IDENTITY_STALE_TEAM_REASON,
)
from courtvision.context.player_identity import (
    BASELINE_PROVIDER_TEAM_CONFLICT_REASON,
    PLAYER_ID_TEAM_CONFLICT_REASON,
    PLAYER_TEAM_NOT_IN_ACTIVE_GAME_REASON,
    SOURCE_IDENTITY_CONFLICT_POLICY_ROW_VALID,
    annotate_source_identity_conflicts,
    build_canonical_player_identity_resolver,
    source_identity_conflict_exposure_summary,
)


def test_resolver_detects_player_id_multiple_teams_and_marks_noncanonical_candidate() -> None:
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-18",
                "game_id": 1,
                "home_team_abbr": "CLE",
                "visitor_team_abbr": "DET",
            }
        ]
    )
    baselines = pd.DataFrame(
        [
            {"player_id": 192, "player_name": "James Harden", "team_abbr": "LAC"},
            {"player_id": 192, "player_name": "James Harden", "team_abbr": "CLE"},
        ]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 1,
                "player_id": 192,
                "player_name": "James Harden",
                "team_abbr": "CLE",
                "market_type": "player_points",
            }
        ]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-18",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )
    annotated = resolver.annotate_records(
        [
            {
                "player_id": 192,
                "player_name": "James Harden",
                "team_abbr": "LAC",
                "game_id": 1,
                "game_home_team_abbr": "CLE",
                "game_away_team_abbr": "DET",
            },
            {
                "player_id": 192,
                "player_name": "James Harden",
                "team_abbr": "CLE",
                "game_id": 1,
                "game_home_team_abbr": "CLE",
                "game_away_team_abbr": "DET",
            },
        ]
    )
    by_team = {row["team_abbr"]: row for row in annotated}

    assert resolver.summary()["counts_by_reason"] == {PLAYER_ID_TEAM_CONFLICT_REASON: 1}
    assert by_team["LAC"]["canonical_team_abbr"] == "CLE"
    assert by_team["LAC"]["player_identity_conflict_reason"] == PLAYER_ID_TEAM_CONFLICT_REASON
    assert by_team["LAC"]["identity_quarantine_reason"] == IDENTITY_OUTSIDE_TEAM_REASON
    assert by_team["LAC"]["candidate_team_not_in_game"] is True
    assert by_team["CLE"]["player_identity_valid"] is True
    assert by_team["CLE"]["player_identity_conflict_reason"] == ""


def test_source_identity_annotation_preserves_row_valid_alignment() -> None:
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-18",
                "game_id": 1,
                "home_team_abbr": "CLE",
                "visitor_team_abbr": "DET",
            }
        ]
    )
    baselines = pd.DataFrame(
        [
            {"player_id": 192, "player_name": "James Harden", "team_abbr": "LAC"},
            {"player_id": 192, "player_name": "James Harden", "team_abbr": "CLE"},
        ]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 1,
                "player_id": 192,
                "player_name": "James Harden",
                "team_abbr": "CLE",
                "provider_team_abbr": "CLE",
                "market_type": "player_points",
            }
        ]
    )
    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-18",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )
    row = resolver.annotate_record(
        {
            "player_id": 192,
            "player_name": "James Harden",
            "team_abbr": "CLE",
            "provider_team_abbr": "CLE",
            "game_id": 1,
            "game_home_team_abbr": "CLE",
            "game_away_team_abbr": "DET",
        }
    )

    annotated = annotate_source_identity_conflicts(pd.DataFrame([row]), resolver.summary())
    out = annotated.iloc[0]

    assert bool(out["player_identity_valid"]) is True
    assert bool(out["row_identity_valid"]) is True
    assert bool(out["row_identity_quarantined"]) is False
    assert bool(out["source_identity_conflicted"]) is False
    assert out["source_identity_conflict_reason"] == ""
    assert out["source_identity_conflict_policy"] == ""


def test_source_identity_exposure_counts_rows_and_unique_players_by_lane() -> None:
    payload = {
        "conflict_count": 1,
        "diagnostic_rows": [
            {
                "player_id": "192",
                "player_name": "James Harden",
                "player_identity_conflict_reason": PLAYER_ID_TEAM_CONFLICT_REASON,
                "player_identity_conflict_details": '{"baseline_team_abbrs":["CLE","LAC"]}',
            }
        ],
    }
    base = {
        "player_id": "192",
        "player_name": "James Harden",
        "team_abbr": "LAC",
        "selection": "over",
    }

    summary = source_identity_conflict_exposure_summary(
        source_identity_payload=payload,
        full_market_df=pd.DataFrame(
            [
                {**base, "market_type": "player_points"},
                {**base, "market_type": "player_rebounds"},
            ]
        ),
        elite_df=pd.DataFrame([{**base, "market_type": "player_points"}]),
        kelly_df=pd.DataFrame([{**base, "market_type": "player_points"}]),
        high_caution_watchlist_df=pd.DataFrame([{**base, "market_type": "player_points"}]),
        combo_under_watchlist_df=pd.DataFrame([{**base, "market_type": "player_points_rebounds"}]),
        paper_kelly_df=pd.DataFrame([{**base, "market_type": "player_points"}]),
    )

    assert summary["source_identity_conflicted_full_market_rows"] == 2
    assert summary["source_identity_conflicted_full_market_players"] == 1
    assert summary["source_identity_conflicted_watchlist_rows"] == 2
    assert summary["source_identity_conflicted_watchlist_players"] == 1
    assert summary["source_identity_conflicted_elite_rows"] == 1
    assert summary["source_identity_conflicted_elite_players"] == 1
    assert summary["source_identity_conflicted_kelly_rows"] == 1
    assert summary["source_identity_conflicted_kelly_players"] == 1
    assert summary["source_identity_conflicted_paper_rows"] == 1
    assert summary["source_identity_conflicted_paper_players"] == 1

    examples = summary["source_identity_conflict_examples"]
    assert len(examples) == 5
    assert examples[0]["player_id"] == "192"
    assert examples[0]["player_name"] == "James Harden"
    assert examples[0]["lane"] == "full_market"
    assert {example["lane"] for example in examples}.issuperset({"full_market", "elite", "kelly"})


def test_resolver_detects_provider_player_id_multiple_teams() -> None:
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-18",
                "game_id": 11,
                "home_team_abbr": "BOS",
                "visitor_team_abbr": "NYK",
            }
        ]
    )
    baselines = pd.DataFrame(
        [{"player_id": 99, "player_name": "Jayson Tatum", "team_abbr": "BOS"}]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 11,
                "player_id": 99,
                "player_name": "Jayson Tatum",
                "team_abbr": "BOS",
                "provider_team_abbr": "BOS",
                "market_type": "player_points",
            },
            {
                "game_id": 11,
                "player_id": 99,
                "player_name": "Jayson Tatum",
                "team_abbr": "NYK",
                "provider_team_abbr": "NYK",
                "market_type": "player_rebounds",
            },
        ]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-18",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )
    annotated = resolver.annotate_records(
        [
            {
                "player_id": 99,
                "player_name": "Jayson Tatum",
                "team_abbr": "BOS",
                "provider_team_abbr": "BOS",
                "game_id": 11,
                "game_home_team_abbr": "BOS",
                "game_away_team_abbr": "NYK",
            },
            {
                "player_id": 99,
                "player_name": "Jayson Tatum",
                "team_abbr": "NYK",
                "provider_team_abbr": "NYK",
                "game_id": 11,
                "game_home_team_abbr": "BOS",
                "game_away_team_abbr": "NYK",
            },
        ]
    )
    by_team = {row["team_abbr"]: row for row in annotated}

    assert resolver.summary()["counts_by_reason"] == {
        BASELINE_PROVIDER_TEAM_CONFLICT_REASON: 1,
        PLAYER_ID_TEAM_CONFLICT_REASON: 1,
    }
    assert by_team["BOS"]["player_identity_valid"] is True
    assert by_team["NYK"]["player_identity_conflict_reason"] == PLAYER_ID_TEAM_CONFLICT_REASON
    assert by_team["NYK"]["identity_quarantine_reason"] == IDENTITY_STALE_TEAM_REASON


def test_resolver_detects_stale_baseline_provider_team_conflict() -> None:
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-18",
                "game_id": 2,
                "home_team_abbr": "CLE",
                "visitor_team_abbr": "LAC",
            }
        ]
    )
    baselines = pd.DataFrame(
        [{"player_id": 192, "player_name": "James Harden", "team_abbr": "CLE"}]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 2,
                "player_id": 192,
                "player_name": "James Harden",
                "team_abbr": "LAC",
                "provider_team_abbr": "LAC",
                "market_type": "player_points",
            }
        ]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-18",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )
    annotated = resolver.annotate_record(
        {
            "player_id": 192,
            "player_name": "James Harden",
            "team_abbr": "CLE",
            "baseline_team_abbr": "CLE",
            "provider_team_abbr": "LAC",
            "game_id": 2,
            "game_home_team_abbr": "CLE",
            "game_away_team_abbr": "LAC",
        }
    )

    assert resolver.summary()["counts_by_reason"] == {BASELINE_PROVIDER_TEAM_CONFLICT_REASON: 1}
    assert annotated["player_identity_valid"] is False
    assert annotated["player_identity_conflict_reason"] == BASELINE_PROVIDER_TEAM_CONFLICT_REASON
    assert annotated["identity_quarantine_reason"] == IDENTITY_STALE_TEAM_REASON


def test_resolver_keeps_clean_valid_player_clear() -> None:
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-18",
                "game_id": 3,
                "home_team_abbr": "BOS",
                "visitor_team_abbr": "NYK",
            }
        ]
    )
    baselines = pd.DataFrame(
        [{"player_id": 99, "player_name": "Jayson Tatum", "team_abbr": "BOS"}]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 3,
                "player_id": 99,
                "player_name": "Jayson Tatum",
                "team_abbr": "BOS",
                "provider_team_abbr": "BOS",
                "market_type": "player_points",
            }
        ]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-18",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )
    annotated = resolver.annotate_record(
        {
            "player_id": 99,
            "player_name": "Jayson Tatum",
            "team_abbr": "BOS",
            "baseline_team_abbr": "BOS",
            "provider_team_abbr": "BOS",
            "game_id": 3,
            "game_home_team_abbr": "BOS",
            "game_away_team_abbr": "NYK",
        }
    )

    assert resolver.summary()["status"] == "ok"
    assert annotated["canonical_team_abbr"] == "BOS"
    assert annotated["player_identity_valid"] is True
    assert annotated["player_identity_conflict_reason"] == ""
    assert "identity_quarantine_reason" not in annotated


def test_resolver_detects_provider_team_outside_active_game() -> None:
    games = pd.DataFrame(
        [{"prediction_date": "2026-05-18", "game_id": 4, "home_team_abbr": "CLE", "visitor_team_abbr": "DET"}]
    )
    baselines = pd.DataFrame(
        [{"player_id": 17, "player_name": "Dennis Schroder", "team_abbr": "SAC"}]
    )
    odds = pd.DataFrame(
        [{"game_id": 4, "player_id": 17, "player_name": "Dennis Schroder", "team_abbr": "SAC"}]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-18",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )

    assert resolver.summary()["counts_by_reason"] == {PLAYER_TEAM_NOT_IN_ACTIVE_GAME_REASON: 1}
