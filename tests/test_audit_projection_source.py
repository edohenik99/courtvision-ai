from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import scripts.audit_projection_source as projection_audit
from scripts.audit_projection_source import (
    PROJECTION_AUDIT_DANGEROUS_DUPLICATES,
    PROJECTION_AUDIT_OK,
    PROJECTION_AUDIT_SOURCE_MISSING,
    run_projection_source_audit,
)


PREDICTION_DATE = "2026-06-05"


def _write_source(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "outputs" / "model" / "player_baselines.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _run(tmp_path: Path, source_path: Path):
    return run_projection_source_audit(
        target_date=PREDICTION_DATE,
        projection_source=source_path,
        output_dir=tmp_path / "outputs" / "runtime" / "research",
        diagnostics_dir=tmp_path / "outputs" / "runtime" / "diagnostics",
    )


def test_duplicate_normalized_names_are_detected_and_same_player_is_deduped(
    tmp_path: Path,
) -> None:
    source_path = _write_source(
        tmp_path,
        [
            {
                "player_id": 12,
                "player_name": "Karl-Anthony Towns",
                "team_abbr": "NYK",
                "pts_recent": 24.0,
                "min_avg": 33.0,
            },
            {
                "player_id": 12,
                "player_name": "Karl Anthony Towns",
                "team_abbr": "NYK",
                "pts_recent": 22.0,
                "min_avg": 31.0,
            },
            {
                "player_id": 34,
                "player_name": "Unique Player",
                "team_abbr": "BOS",
                "pts_recent": 15.0,
                "min_avg": 28.0,
            },
        ],
    )

    result = _run(tmp_path, source_path)

    assert result.status == PROJECTION_AUDIT_OK
    diagnostics = result.diagnostics
    assert diagnostics["row_count"] == 3
    assert diagnostics["unique_normalized_player_count"] == 2
    assert diagnostics["duplicate_normalized_player_count"] == 1
    assert diagnostics["dangerous_duplicate_count"] == 0
    duplicate = diagnostics["duplicate_players_sample"][0]
    assert duplicate["normalized_player_name"] == "karl anthony towns"
    assert duplicate["classification"] == "likely_historical_rows"
    assert duplicate["player_ids"] == ["12"]

    cleaned = pd.read_csv(result.cleaned_context_path)
    assert len(cleaned.index) == 2
    assert cleaned["normalized_player_name"].is_unique
    towns = cleaned[
        cleaned["normalized_player_name"] == "karl anthony towns"
    ].iloc[0]
    assert towns["duplicate_count"] == 2
    assert towns["dedupe_reason"] == "most_complete_recent_minutes_context"


def test_dangerous_duplicates_with_different_player_ids_are_detected(
    tmp_path: Path,
) -> None:
    source_path = _write_source(
        tmp_path,
        [
            {
                "player_id": 100,
                "player_name": "Jalen Williams",
                "team_abbr": "OKC",
                "pts_avg": 20.0,
            },
            {
                "player_id": 200,
                "player_name": "Jalen Williams",
                "team_abbr": "CHI",
                "pts_avg": 10.0,
            },
        ],
    )

    result = _run(tmp_path, source_path)

    assert result.status == PROJECTION_AUDIT_DANGEROUS_DUPLICATES
    assert result.diagnostics["dangerous_duplicate_count"] == 1
    dangerous = result.diagnostics["dangerous_duplicates_sample"][0]
    assert dangerous["player_ids"] == ["100", "200"]
    assert dangerous["classification"] == "dangerous_different_player_id"


def test_dedupe_prefers_most_recent_context_date(tmp_path: Path) -> None:
    source_path = _write_source(
        tmp_path,
        [
            {
                "player_id": 12,
                "player_name": "Recent Context",
                "team_abbr": "TOR",
                "context_date": "2026-05-01",
                "pts_recent": 12.0,
            },
            {
                "player_id": 12,
                "player_name": "Recent Context",
                "team_abbr": "TOR",
                "context_date": "2026-06-01",
                "pts_recent": 18.0,
            },
        ],
    )

    result = _run(tmp_path, source_path)

    cleaned = pd.read_csv(result.cleaned_context_path)
    assert len(cleaned.index) == 1
    assert cleaned.iloc[0]["pts_recent"] == 18.0
    assert cleaned.iloc[0]["dedupe_reason"] == "most_recent_context:context_date"


def test_multi_team_same_player_needs_review_without_becoming_dangerous(
    tmp_path: Path,
) -> None:
    source_path = _write_source(
        tmp_path,
        [
            {
                "player_id": 12,
                "player_name": "Team Change",
                "team_abbr": "UTA",
                "pts_recent": 14.0,
            },
            {
                "player_id": 12,
                "player_name": "Team Change",
                "team_abbr": "MEM",
                "pts_recent": 11.0,
            },
        ],
    )

    result = _run(tmp_path, source_path)

    assert result.status == PROJECTION_AUDIT_OK
    detail = result.diagnostics["duplicate_players_sample"][0]
    assert detail["different_player_id"] is False
    assert detail["different_team_abbr"] is True
    assert detail["classification"] == "needs_review_different_team_abbr"
    assert any("span multiple teams" in warning for warning in result.diagnostics["warnings"])


def test_missing_source_is_non_fatal_and_writes_empty_artifacts(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "missing.csv"

    result = _run(tmp_path, source_path)

    assert result.status == PROJECTION_AUDIT_SOURCE_MISSING
    assert result.audit_path.exists()
    assert result.cleaned_context_path.exists()
    assert result.diagnostics_path.exists()
    cleaned = pd.read_csv(result.cleaned_context_path)
    assert cleaned.empty
    diagnostics = json.loads(result.diagnostics_path.read_text(encoding="utf-8"))
    assert diagnostics["status"] == PROJECTION_AUDIT_SOURCE_MISSING
    assert diagnostics["row_count"] == 0
    assert diagnostics["cleaned_context_row_count"] == 0


def test_audit_reports_stat_columns_and_writes_no_betting_artifacts(
    tmp_path: Path,
) -> None:
    source_path = _write_source(
        tmp_path,
        [
            {
                "player_id": 12,
                "player_name": "Context Player",
                "team_abbr": "TOR",
                "pts_avg": 16.0,
                "pts_recent": 18.0,
                "reb_avg": 7.0,
                "reb_recent": 8.0,
                "ast_avg": 5.0,
                "ast_recent": 6.0,
                "min_avg": 31.0,
                "min_recent": 33.0,
            }
        ],
    )
    operator_dir = tmp_path / "outputs" / "runtime" / "operator"
    operator_dir.mkdir(parents=True)
    kelly_sentinel = operator_dir / f"kelly_stakes_{PREDICTION_DATE}.csv"
    elite_sentinel = operator_dir / f"elite_board_{PREDICTION_DATE}.csv"
    kelly_sentinel.write_text("existing kelly\n", encoding="utf-8")
    elite_sentinel.write_text("existing elite\n", encoding="utf-8")

    result = _run(tmp_path, source_path)

    diagnostics = result.diagnostics
    assert diagnostics["available_stat_columns"] == {
        "points": ["pts_avg", "pts_recent"],
        "rebounds": ["reb_avg", "reb_recent"],
        "assists": ["ast_avg", "ast_recent"],
        "recent_averages": ["pts_recent", "reb_recent", "ast_recent"],
        "baseline_values": ["pts_avg", "reb_avg", "ast_avg"],
        "minutes_averages": ["min_avg", "min_recent"],
        "player_id": ["player_id"],
        "team_abbr": ["team_abbr"],
    }
    assert diagnostics["missing_expected_columns"] == []
    assert diagnostics["eligible_for_betting_any_true"] is False
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False
    assert diagnostics["operator_betting_boards_written"] == []
    assert not hasattr(projection_audit, "MarketProp")
    assert kelly_sentinel.read_text(encoding="utf-8") == "existing kelly\n"
    assert elite_sentinel.read_text(encoding="utf-8") == "existing elite\n"
    assert sorted(path.name for path in operator_dir.iterdir()) == [
        f"elite_board_{PREDICTION_DATE}.csv",
        f"kelly_stakes_{PREDICTION_DATE}.csv",
    ]
