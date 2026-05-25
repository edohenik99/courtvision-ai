from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.prefill_actual_feedback import prefill_actual_feedback_for_date


class _DummyClient:
    def __init__(self, *, stats: pd.DataFrame, games: pd.DataFrame) -> None:
        self.stats = stats
        self.games = games

    def get_games(self, prediction_date: str) -> pd.DataFrame:
        return self.games.copy()

    def get_stats(self, start_date: str, end_date: str) -> pd.DataFrame:
        return self.stats.copy()


class _DummyAI:
    def __init__(self, *, stats: pd.DataFrame, games: pd.DataFrame) -> None:
        self.client = _DummyClient(stats=stats, games=games)

    def _get_client(self) -> _DummyClient:
        return self.client

    def _normalize_stats(self, raw_stats: pd.DataFrame) -> pd.DataFrame:
        return raw_stats.copy()

    def _append_history(self, path: Path, df: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = pd.read_csv(path, keep_default_na=False, low_memory=False)
            pd.concat([existing, df], ignore_index=True).to_csv(path, index=False)
        else:
            df.to_csv(path, index=False)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_prefill_actual_feedback_writes_final_rows_from_existing_full_market_board(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    prediction_date = "2026-05-23"
    _write_csv(
        runtime_root / "operator" / f"full_market_board_{prediction_date}.csv",
        [
            {
                "prediction_date": prediction_date,
                "player_name": "Board Player",
                "entity_name": "Board Player",
                "player_id": "11",
                "team_abbr": "NYK",
                "opponent": "CLE",
                "game_id": "21713899",
                "market_type": "player_points",
                "selection": "over",
                "line": 10.5,
            },
            {
                "prediction_date": prediction_date,
                "player_name": "Board Player",
                "entity_name": "Board Player",
                "player_id": "11",
                "team_abbr": "NYK",
                "opponent": "CLE",
                "game_id": "21713899",
                "market_type": "player_rebounds",
                "selection": "under",
                "line": 4.5,
            },
        ],
    )
    stats = pd.DataFrame(
        [
            {
                "player_id": "11",
                "player_name": "Board Player",
                "team_abbr": "NYK",
                "game_id": "21713899",
                "pts": 12,
                "reb": 6,
                "ast": 2,
                "blk": 0,
                "stl": 1,
            }
        ]
    )
    games = pd.DataFrame(
        [
            {
                "game_id": "21713899",
                "status": "Final",
                "home_team_score": 108,
                "visitor_team_score": 121,
            }
        ]
    )

    result = prefill_actual_feedback_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        ai_factory=lambda _out_dir: _DummyAI(stats=stats, games=games),
    )

    feedback = pd.read_csv(runtime_root / "history" / "result_feedback.csv", keep_default_na=False)
    assert result["status"] == "ok"
    assert result["prefilled_rows"] == 2
    assert set(feedback["result"]) == {"win", "loss"}
    assert set(feedback["player_id"].astype(str)) == {"11"}
    assert set(feedback["game_id"].astype(str)) == {"21713899"}
    assert feedback.loc[feedback["market_type"] == "player_points", "actual_value"].astype(float).iloc[0] == 12.0
    assert feedback.loc[feedback["market_type"] == "player_rebounds", "actual_value"].astype(float).iloc[0] == 6.0


def test_prefill_actual_feedback_returns_zero_when_provider_stats_missing(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    prediction_date = "2026-05-23"
    _write_csv(
        runtime_root / "operator" / f"full_market_board_{prediction_date}.csv",
        [
            {
                "prediction_date": prediction_date,
                "player_name": "Board Player",
                "player_id": "11",
                "team_abbr": "NYK",
                "game_id": "21713899",
                "market_type": "player_points",
                "selection": "over",
                "line": 10.5,
            }
        ],
    )

    result = prefill_actual_feedback_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        ai_factory=lambda _out_dir: _DummyAI(stats=pd.DataFrame(), games=pd.DataFrame()),
    )

    assert result["status"] == "provider_stats_empty"
    assert result["prefilled_rows"] == 0
    assert not (runtime_root / "history" / "result_feedback.csv").exists()
