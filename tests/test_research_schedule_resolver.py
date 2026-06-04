from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.providers.research_schedule_resolver import (
    PROVIDER_STATUS_MISSING,
    SOURCE_API_NBA,
    SOURCE_MANUAL_SCHEDULE,
    SOURCE_NONE,
    resolve_research_schedule,
)


def _api_nba_body(target_date: str, game_id: int = 10403) -> dict[str, Any]:
    return {
        "response": [
            {
                "id": game_id,
                "date": {"start": f"{target_date}T00:00:00+00:00"},
                "teams": {
                    "home": {"name": "Oklahoma City Thunder", "code": "OKC"},
                    "visitors": {"name": "Indiana Pacers", "code": "IND"},
                },
            }
        ]
    }


def _write_manual_schedule(
    manual_dir: Path,
    target_date: str,
    rows: list[dict[str, Any]],
) -> Path:
    manual_dir.mkdir(parents=True, exist_ok=True)
    path = manual_dir / f"manual_games_{target_date}.csv"
    pd.DataFrame(
        rows,
        columns=[
            "game_date",
            "game_id",
            "home_team",
            "away_team",
            "home_team_abbr",
            "away_team_abbr",
            "source",
        ],
    ).to_csv(path, index=False)
    return path


def _manual_row(target_date: str, game_id: str = "manual_2026-06-03_001") -> dict[str, str]:
    return {
        "game_date": target_date,
        "game_id": game_id,
        "home_team": "Oklahoma City Thunder",
        "away_team": "Indiana Pacers",
        "home_team_abbr": "OKC",
        "away_team_abbr": "IND",
        "source": "manual_schedule",
    }


def test_api_nba_schedule_used_when_available(tmp_path: Path) -> None:
    target_date = "2026-06-03"
    manual_dir = tmp_path / "data" / "manual_schedule"
    runtime_root = tmp_path / "outputs" / "runtime"
    _write_manual_schedule(manual_dir, target_date, [_manual_row(target_date)])

    result = resolve_research_schedule(
        target_date,
        _api_nba_body(target_date),
        manual_schedule_dir=manual_dir,
        runtime_root=runtime_root,
    )

    assert result.selected_source == SOURCE_API_NBA
    assert result.provider_status == "ok"
    assert len(result.schedule) == 1
    row = result.schedule.iloc[0].to_dict()
    assert row["game_id"] == "10403"
    assert row["source"] == SOURCE_API_NBA
    assert row["mode"] == "research"
    assert row["eligible_for_betting"] is False

    diagnostics_path = runtime_root / "diagnostics" / f"research_schedule_resolver_{target_date}.json"
    payload = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert payload == {
        "target_date": target_date,
        "api_nba_games_available": True,
        "manual_schedule_available": True,
        "selected_source": SOURCE_API_NBA,
        "game_count": 1,
        "provider_status": "ok",
        "warnings": [],
    }


def test_manual_schedule_fallback_used_when_api_nba_games_missing(tmp_path: Path) -> None:
    target_date = "2026-06-03"
    manual_dir = tmp_path / "data" / "manual_schedule"
    runtime_root = tmp_path / "outputs" / "runtime"
    _write_manual_schedule(manual_dir, target_date, [_manual_row(target_date)])

    result = resolve_research_schedule(
        target_date,
        {"response": []},
        manual_schedule_dir=manual_dir,
        runtime_root=runtime_root,
    )

    assert result.selected_source == SOURCE_MANUAL_SCHEDULE
    assert result.provider_status == "ok"
    assert result.diagnostics["api_nba_games_available"] is False
    assert len(result.schedule) == 1
    row = result.schedule.iloc[0].to_dict()
    assert row["source"] == SOURCE_MANUAL_SCHEDULE
    assert row["mode"] == "research"
    assert row["eligible_for_betting"] is False


def test_missing_api_nba_games_and_missing_manual_csv_is_non_fatal(tmp_path: Path) -> None:
    target_date = "2026-06-03"
    runtime_root = tmp_path / "outputs" / "runtime"

    result = resolve_research_schedule(
        target_date,
        None,
        manual_schedule_dir=tmp_path / "missing_manual_schedule",
        runtime_root=runtime_root,
    )

    assert result.selected_source == SOURCE_NONE
    assert result.provider_status == PROVIDER_STATUS_MISSING
    assert result.schedule.empty
    assert result.market_props == []
    assert result.diagnostics["game_count"] == 0
    assert result.diagnostics["warnings"]


def test_manual_schedule_validation_rejects_bad_rows(tmp_path: Path) -> None:
    target_date = "2026-06-03"
    manual_dir = tmp_path / "data" / "manual_schedule"
    bad_row = _manual_row(target_date, game_id="bad_row")
    bad_row["home_team"] = ""
    wrong_source = _manual_row(target_date, game_id="wrong_source")
    wrong_source["source"] = "nba_dot_com"
    _write_manual_schedule(
        manual_dir,
        target_date,
        [_manual_row(target_date), bad_row, wrong_source],
    )

    result = resolve_research_schedule(
        target_date,
        {"response": []},
        manual_schedule_dir=manual_dir,
        runtime_root=tmp_path / "outputs" / "runtime",
    )

    assert result.selected_source == SOURCE_MANUAL_SCHEDULE
    assert result.schedule["game_id"].tolist() == ["manual_2026-06-03_001"]
    warnings = "\n".join(result.diagnostics["warnings"])
    assert "manual_schedule_row_rejected line=3" in warnings
    assert "missing_home_team" in warnings
    assert "manual_schedule_row_rejected line=4" in warnings
    assert "invalid_source" in warnings


def test_fallback_rows_are_tagged_ineligible_for_betting(tmp_path: Path) -> None:
    target_date = "2026-06-03"
    manual_dir = tmp_path / "data" / "manual_schedule"
    _write_manual_schedule(manual_dir, target_date, [_manual_row(target_date)])

    result = resolve_research_schedule(
        target_date,
        None,
        manual_schedule_dir=manual_dir,
        runtime_root=tmp_path / "outputs" / "runtime",
    )

    assert result.schedule["source"].tolist() == [SOURCE_MANUAL_SCHEDULE]
    assert result.schedule["mode"].tolist() == ["research"]
    assert result.schedule["eligible_for_betting"].tolist() == [False]


def test_no_marketprop_rows_are_produced(tmp_path: Path) -> None:
    target_date = "2026-06-03"

    result = resolve_research_schedule(
        target_date,
        _api_nba_body(target_date),
        manual_schedule_dir=tmp_path / "data" / "manual_schedule",
        runtime_root=tmp_path / "outputs" / "runtime",
    )

    assert result.market_props == []
    assert all(type(item).__name__ != "MarketProp" for item in result.market_props)
