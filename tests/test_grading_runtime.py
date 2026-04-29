from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision_ai import CourtVisionAI, _write_grading_outputs


class FakeGradingClient:
    def __init__(self, stats: pd.DataFrame, games: pd.DataFrame) -> None:
        self.stats = stats
        self.games = games
        self.stats_calls = 0
        self.games_calls = 0

    def get_stats(self, start_date: str, end_date: str) -> pd.DataFrame:
        self.stats_calls += 1
        return self.stats.copy()

    def get_games(self, game_date: str) -> pd.DataFrame:
        self.games_calls += 1
        return self.games.copy()


def _history_row(
    *,
    player: str = "Alpha Player",
    team: str = "BOS",
    selection: str = "over",
    line: float = 20.5,
    prediction_date: str = "2026-04-28",
) -> dict[str, object]:
    return {
        "prediction_date": prediction_date,
        "player_name": player,
        "entity_name": player,
        "team": team,
        "market_type": "player_points",
        "selection": selection,
        "sportsbook_line": line,
        "odds": -110,
        "confidence": 0.75,
        "quality_score": 80.0,
        "edge": 2.0,
    }


def _grade_key(row: dict[str, object]) -> str:
    return (
        f"{row['prediction_date']}|{row['market_type']}|{row['entity_name']}|"
        f"{row['selection']}|{row['sportsbook_line']}"
    )


def _make_ai(tmp_path: Path, history_rows: list[dict[str, object]], feedback_rows: list[dict[str, object]] | None = None) -> tuple[CourtVisionAI, FakeGradingClient]:
    ai = CourtVisionAI.__new__(CourtVisionAI)
    ai.prediction_history_path = tmp_path / "prediction_history.csv"
    ai.feedback_path = tmp_path / "result_feedback.csv"
    pd.DataFrame(history_rows).to_csv(ai.prediction_history_path, index=False)
    if feedback_rows is not None:
        pd.DataFrame(feedback_rows).to_csv(ai.feedback_path, index=False)

    stats = pd.DataFrame(
        [
            {
                "player_name": "Alpha Player",
                "team_abbr": "BOS",
                "pts": 22.0,
                "reb": 5.0,
                "ast": 4.0,
                "fg3m": 2.0,
                "stl": 1.0,
                "blk": 0.0,
            }
        ]
    )
    games = pd.DataFrame(
        [
            {
                "status": "Final",
                "home_team_score": 110,
                "visitor_team_score": 101,
                "home_team_abbr": "BOS",
                "visitor_team_abbr": "NYK",
            }
        ]
    )
    client = FakeGradingClient(stats=stats, games=games)
    ai._get_client = lambda: client  # type: ignore[method-assign]
    ai._normalize_stats = lambda df: df.copy()  # type: ignore[method-assign]
    return ai, client


def test_unresolved_feedback_row_is_reprocessed(tmp_path: Path) -> None:
    row = _history_row(selection="over", line=20.5)
    ai, _ = _make_ai(
        tmp_path,
        [row],
        [{"grade_key": _grade_key(row), "result": "unresolved", "graded_result": "unresolved"}],
    )

    graded, summary = ai._grade_history("2026-04-29", lookback_days=14)

    diagnostics = summary["diagnostics"]
    assert len(graded) == 1
    assert graded.iloc[0]["result"] == "win"
    assert diagnostics["existing_feedback_unresolved_rows"] == 1
    assert diagnostics["reprocess_unresolved_rows"] == 1
    assert diagnostics["skipped_final_feedback_rows"] == 0
    assert diagnostics["matched_picks"] == 1
    assert diagnostics["graded_rows_written"] == 1


def test_final_feedback_row_is_skipped(tmp_path: Path) -> None:
    row = _history_row(selection="over", line=20.5)
    ai, client = _make_ai(
        tmp_path,
        [row],
        [{"grade_key": _grade_key(row), "result": "win", "graded_result": "win"}],
    )

    graded, summary = ai._grade_history("2026-04-29", lookback_days=14)

    diagnostics = summary["diagnostics"]
    assert graded.empty
    assert diagnostics["existing_feedback_final_rows"] == 1
    assert diagnostics["skipped_final_feedback_rows"] == 1
    assert diagnostics["pending_rows"] == 0
    assert client.stats_calls == 0
    assert client.games_calls == 0


def test_lowercase_over_grades_correctly() -> None:
    ai = CourtVisionAI.__new__(CourtVisionAI)
    graded = ai._grade_single_prediction(
        _history_row(selection="over", line=20.5),
        pd.DataFrame([{"player_name": "Alpha Player", "team_abbr": "BOS", "pts": 22.0}]),
        [],
    )

    assert graded is not None
    assert graded["selection"] == "over"
    assert graded["result"] == "win"
    assert graded["is_win"] == 1


def test_lowercase_under_grades_correctly() -> None:
    ai = CourtVisionAI.__new__(CourtVisionAI)
    graded = ai._grade_single_prediction(
        _history_row(selection="under", line=20.5),
        pd.DataFrame([{"player_name": "Alpha Player", "team_abbr": "BOS", "pts": 18.0}]),
        [],
    )

    assert graded is not None
    assert graded["selection"] == "under"
    assert graded["result"] == "win"
    assert graded["is_win"] == 1


def test_mixed_case_over_under_grade_correctly() -> None:
    ai = CourtVisionAI.__new__(CourtVisionAI)

    over = ai._grade_single_prediction(
        _history_row(selection="Over", line=20.5),
        pd.DataFrame([{"player_name": "Alpha Player", "team_abbr": "BOS", "pts": 22.0}]),
        [],
    )
    under = ai._grade_single_prediction(
        _history_row(selection="UNDER", line=20.5),
        pd.DataFrame([{"player_name": "Alpha Player", "team_abbr": "BOS", "pts": 18.0}]),
        [],
    )

    assert over is not None
    assert under is not None
    assert over["result"] == "win"
    assert under["result"] == "win"


def test_empty_grading_results_csv_is_valid_when_no_rows_gradeable(tmp_path: Path) -> None:
    paths = _write_grading_outputs(tmp_path, "2026-04-29", pd.DataFrame())

    assert paths["grading_results"].exists()
    assert paths["grading_summary_json"].exists()
    payload = json.loads(paths["grading_summary_json"].read_text(encoding="utf-8"))
    assert payload["overall"]["n"] == 0
