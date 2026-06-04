from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import scripts.run_market_validation as market_validation
from scripts.run_market_validation import (
    BOARD_COLUMNS,
    MARKET_VALIDATION_NO_EVENTS,
    MARKET_VALIDATION_NO_PROPS,
    MARKET_VALIDATION_OK,
    MARKET_VALIDATION_PARTIAL_MARKET_COVERAGE,
    MARKET_VALIDATION_PROVIDER_UNAVAILABLE,
    MARKET_VALIDATION_SCHEMA_INVALID,
    run_market_validation,
)


PREDICTION_DATE = "2026-06-04"


def _row(
    *,
    player_name: str = "Jane Doe",
    market_type: str = "player_points",
    side: str = "over",
    line: float = 25.5,
    american_odds: int = -110,
    sportsbook: str = "DraftKings",
    eligible_for_betting: bool = False,
) -> dict[str, Any]:
    return {
        "provider": "the_odds_api",
        "provider_event_id": "evt_target",
        "home_team": "Oklahoma City Thunder",
        "away_team": "Indiana Pacers",
        "game_date": PREDICTION_DATE,
        "commence_time_utc": "2026-06-04T23:30:00Z",
        "commence_time_local": "2026-06-04T19:30:00-04:00",
        "player_name": player_name,
        "market_type": market_type,
        "side": side,
        "line": line,
        "american_odds": american_odds,
        "sportsbook": sportsbook,
        "updated_at": "2026-06-04T14:01:00Z",
        "source": "the_odds_api:event_odds",
        "eligible_for_betting": eligible_for_betting,
    }


def _output_dir(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime" / "research"


def _runtime_root(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime"


def _write_provider_diagnostics(
    runtime_root: Path,
    *,
    provider_status: str = "ok",
    target_date_events_count: int = 1,
    prop_row_count: int = 0,
) -> None:
    path = runtime_root / "diagnostics" / f"the_odds_api_provider_{PREDICTION_DATE}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "provider_status": provider_status,
                "target_date": PREDICTION_DATE,
                "events_available": target_date_events_count,
                "target_date_events_count": target_date_events_count,
                "probed_event_count": 1 if target_date_events_count else 0,
                "prop_row_count": prop_row_count,
            }
        ),
        encoding="utf-8",
    )


def _loader_for(rows: list[dict[str, Any]], *, provider_status: str = "ok"):
    def loader(
        target_date: str,
        markets: list[str],
        *,
        max_events: int,
        timezone: str,
        runtime_root: Path,
    ) -> pd.DataFrame:
        assert target_date == PREDICTION_DATE
        assert markets
        assert max_events == 1
        assert timezone == "America/Toronto"
        _write_provider_diagnostics(
            Path(runtime_root),
            provider_status=provider_status,
            prop_row_count=len(rows),
        )
        return pd.DataFrame(rows)

    return loader


def _read_diagnostics(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_valid_provider_rows_produce_board_summary_and_diagnostics(tmp_path: Path) -> None:
    rows = [
        _row(market_type="player_points", side="over", sportsbook="DraftKings"),
        _row(market_type="player_points", side="under", sportsbook="DraftKings"),
        _row(market_type="player_rebounds", side="over", line=7.5, sportsbook="DraftKings"),
        _row(market_type="player_rebounds", side="under", line=7.5, sportsbook="DraftKings"),
        _row(market_type="player_assists", side="over", line=6.5, sportsbook="FanDuel"),
        _row(market_type="player_assists", side="under", line=6.5, sportsbook="FanDuel"),
    ]

    result = run_market_validation(
        target_date=PREDICTION_DATE,
        markets="player_points,player_rebounds,player_assists",
        output_dir=_output_dir(tmp_path),
        props_loader=_loader_for(rows),
    )

    assert result.status == MARKET_VALIDATION_OK
    board = pd.read_csv(result.board_path)
    assert board.columns.tolist() == BOARD_COLUMNS
    assert len(board.index) == 6
    assert board["eligible_for_betting"].tolist() == [False] * 6

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["status"] == MARKET_VALIDATION_OK
    assert diagnostics["row_count"] == 6
    assert diagnostics["unique_market_count"] == 3
    assert diagnostics["market_counts"] == {
        "player_assists": 2,
        "player_points": 2,
        "player_rebounds": 2,
    }
    assert diagnostics["sportsbook_market_counts"] == {
        "DraftKings": {"player_points": 2, "player_rebounds": 2},
        "FanDuel": {"player_assists": 2},
    }
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False
    assert diagnostics["operator_artifacts_written"] == []

    summary = result.summary_path.read_text(encoding="utf-8")
    assert "status: MARKET_VALIDATION_OK" in summary
    assert "sportsbook_market_coverage:" in summary
    assert "WARNING: Betting Mode is not integrated" in summary


def test_sparse_market_coverage_gives_partial_status_without_crashing(tmp_path: Path) -> None:
    rows = [
        _row(market_type="player_assists", side="over", line=6.5),
        _row(market_type="player_assists", side="under", line=6.5),
    ]

    result = run_market_validation(
        target_date=PREDICTION_DATE,
        markets="player_points,player_rebounds,player_assists",
        output_dir=_output_dir(tmp_path),
        props_loader=_loader_for(rows),
    )

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert result.status == MARKET_VALIDATION_PARTIAL_MARKET_COVERAGE
    assert diagnostics["missing_requested_markets"] == ["player_points", "player_rebounds"]
    assert diagnostics["returned_markets"] == ["player_assists"]


def test_missing_key_gives_provider_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)

    result = run_market_validation(
        target_date=PREDICTION_DATE,
        markets="player_points",
        output_dir=_output_dir(tmp_path),
    )

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert result.status == MARKET_VALIDATION_PROVIDER_UNAVAILABLE
    assert diagnostics["provider_status"] == "missing_credentials"
    assert diagnostics["row_count"] == 0


def test_no_events_status_uses_provider_diagnostics(tmp_path: Path) -> None:
    def loader(
        target_date: str,
        markets: list[str],
        *,
        max_events: int,
        timezone: str,
        runtime_root: Path,
    ) -> pd.DataFrame:
        _write_provider_diagnostics(
            Path(runtime_root),
            target_date_events_count=0,
            prop_row_count=0,
        )
        return pd.DataFrame([], columns=BOARD_COLUMNS)

    result = run_market_validation(
        target_date=PREDICTION_DATE,
        markets="player_points",
        output_dir=_output_dir(tmp_path),
        props_loader=loader,
    )

    assert result.status == MARKET_VALIDATION_NO_EVENTS


def test_no_props_status_when_events_exist_but_rows_are_empty(tmp_path: Path) -> None:
    def loader(
        target_date: str,
        markets: list[str],
        *,
        max_events: int,
        timezone: str,
        runtime_root: Path,
    ) -> pd.DataFrame:
        _write_provider_diagnostics(
            Path(runtime_root),
            target_date_events_count=1,
            prop_row_count=0,
        )
        return pd.DataFrame([], columns=BOARD_COLUMNS)

    result = run_market_validation(
        target_date=PREDICTION_DATE,
        markets="player_points",
        output_dir=_output_dir(tmp_path),
        props_loader=loader,
    )

    assert result.status == MARKET_VALIDATION_NO_PROPS


def test_schema_missing_required_columns_gives_schema_invalid(tmp_path: Path) -> None:
    row = _row()
    row.pop("american_odds")

    result = run_market_validation(
        target_date=PREDICTION_DATE,
        markets="player_points",
        output_dir=_output_dir(tmp_path),
        props_loader=_loader_for([row]),
    )

    diagnostics = _read_diagnostics(result.diagnostics_path)
    board = pd.read_csv(result.board_path)
    assert result.status == MARKET_VALIDATION_SCHEMA_INVALID
    assert diagnostics["schema_missing_required_columns"] == ["american_odds"]
    assert diagnostics["rows_missing_american_odds"] == 1
    assert "american_odds" in board.columns


def test_eligible_for_betting_any_true_fails_validation_and_board_is_forced_false(
    tmp_path: Path,
) -> None:
    result = run_market_validation(
        target_date=PREDICTION_DATE,
        markets="player_points",
        output_dir=_output_dir(tmp_path),
        props_loader=_loader_for([_row(eligible_for_betting=True)]),
    )

    diagnostics = _read_diagnostics(result.diagnostics_path)
    board = pd.read_csv(result.board_path)
    assert result.status == MARKET_VALIDATION_SCHEMA_INVALID
    assert diagnostics["eligible_for_betting_any_true"] is True
    assert board["eligible_for_betting"].tolist() == [False]


def test_duplicate_rows_are_counted(tmp_path: Path) -> None:
    duplicate = _row()
    rows = [duplicate, dict(duplicate), _row(side="under")]

    result = run_market_validation(
        target_date=PREDICTION_DATE,
        markets="player_points",
        output_dir=_output_dir(tmp_path),
        props_loader=_loader_for(rows),
    )

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert result.status == MARKET_VALIDATION_OK
    assert diagnostics["duplicate_key_count"] == 1


def test_orphan_over_under_rows_are_counted(tmp_path: Path) -> None:
    rows = [
        _row(player_name="Over Only", side="over"),
        _row(player_name="Under Only", side="under"),
    ]

    result = run_market_validation(
        target_date=PREDICTION_DATE,
        markets="player_points",
        output_dir=_output_dir(tmp_path),
        props_loader=_loader_for(rows),
    )

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["over_count"] == 1
    assert diagnostics["under_count"] == 1
    assert diagnostics["over_under_pair_count"] == 0
    assert diagnostics["orphan_over_count"] == 1
    assert diagnostics["orphan_under_count"] == 1


def test_no_kelly_elite_operator_artifacts_or_marketprop_rows_are_created(
    tmp_path: Path,
) -> None:
    runtime_root = _runtime_root(tmp_path)
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True)
    kelly_sentinel = operator_dir / f"kelly_stakes_{PREDICTION_DATE}.csv"
    elite_sentinel = operator_dir / f"elite_board_{PREDICTION_DATE}.csv"
    kelly_sentinel.write_text("player_name,stake\nExisting,10\n", encoding="utf-8")
    elite_sentinel.write_text("player_name,score\nExisting,99\n", encoding="utf-8")

    result = run_market_validation(
        target_date=PREDICTION_DATE,
        markets="player_points",
        output_dir=runtime_root / "research",
        props_loader=_loader_for([_row(), _row(side="under")]),
    )

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False
    assert diagnostics["operator_artifacts_written"] == []
    assert diagnostics["operator_betting_boards_written"] == []
    assert not hasattr(market_validation, "MarketProp")
    assert kelly_sentinel.read_text(encoding="utf-8") == "player_name,stake\nExisting,10\n"
    assert elite_sentinel.read_text(encoding="utf-8") == "player_name,score\nExisting,99\n"
    assert not (operator_dir / f"full_market_board_{PREDICTION_DATE}.csv").exists()
