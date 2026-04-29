from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision_ai import CourtVisionAI, _write_grading_outputs
from scripts.backfill_grading import backfill_result_feedback


class FakeBackfillClient:
    def __init__(self) -> None:
        self.stats_calls = 0
        self.games_calls = 0

    def get_stats(self, start_date: str, end_date: str) -> pd.DataFrame:
        self.stats_calls += 1
        return pd.DataFrame(
            [
                {
                    "player_name": "Alpha Player",
                    "team_abbr": "BOS",
                    "pts": 22.0,
                    "reb": 8.0,
                },
                {
                    "player_name": "Beta Player",
                    "team_abbr": "NYK",
                    "pts": 17.0,
                    "reb": 4.0,
                },
            ]
        )

    def get_games(self, game_date: str) -> pd.DataFrame:
        self.games_calls += 1
        return pd.DataFrame(
            [
                {
                    "status": "Final",
                    "home_team_abbr": "BOS",
                    "visitor_team_abbr": "NYK",
                    "home_team_score": 112,
                    "visitor_team_score": 104,
                }
            ]
        )


def _feedback_row(
    *,
    prediction_date: str = "2026-04-28",
    player: str = "Alpha Player",
    team: str = "BOS",
    market_type: str = "player_points",
    selection: str = "over",
    line: float = 20.5,
    result: str = "unresolved",
) -> dict[str, object]:
    grade_key = f"{prediction_date}|{market_type}|{player}|{selection}|{line}"
    return {
        "grade_key": grade_key,
        "prediction_date": prediction_date,
        "market_type": market_type,
        "entity_name": player,
        "player_name": player,
        "team": team,
        "opponent": "NYK" if team == "BOS" else "BOS",
        "selection": selection,
        "sportsbook_line": line,
        "odds": -110,
        "confidence": 0.72,
        "quality_score": 81.0,
        "edge": 2.1,
        "actual_value": "",
        "result": result,
        "graded_result": result,
        "is_win": 1 if result == "win" else 0,
        "is_push": 1 if result == "push" else 0,
        "is_loss": 1 if result == "loss" else 0,
        "ungraded_reason": "",
    }


def _make_ai_factory(client: FakeBackfillClient):
    def factory(out_dir: Path) -> CourtVisionAI:
        ai = CourtVisionAI.__new__(CourtVisionAI)
        ai.out_dir = out_dir
        ai.runtime_dir = out_dir / "runtime"
        ai.runtime_history_dir = ai.runtime_dir / "history"
        ai.feedback_path = ai.runtime_history_dir / "result_feedback.csv"
        ai._get_client = lambda: client  # type: ignore[method-assign]
        ai._normalize_stats = lambda df: df.copy()  # type: ignore[method-assign]
        return ai

    return factory


def _write_feedback(out_dir: Path, rows: list[dict[str, object]]) -> Path:
    path = out_dir / "runtime" / "history" / "result_feedback.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    feedback_path = _write_feedback(out_dir, [_feedback_row()])
    before = feedback_path.read_text(encoding="utf-8")
    client = FakeBackfillClient()

    stats = backfill_result_feedback(
        runtime_root=out_dir / "runtime",
        out_dir=out_dir,
        write=False,
        ai_factory=_make_ai_factory(client),
    )

    assert stats.total_updated == 1
    assert feedback_path.read_text(encoding="utf-8") == before
    assert not (out_dir / "runtime" / "research" / "grading_results_2026-04-28.csv").exists()


def test_write_mode_updates_unresolved_over_under_rows(tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    feedback_path = _write_feedback(out_dir, [_feedback_row(selection="over", line=20.5)])

    stats = backfill_result_feedback(
        runtime_root=out_dir / "runtime",
        out_dir=out_dir,
        write=True,
        ai_factory=_make_ai_factory(FakeBackfillClient()),
    )

    updated = pd.read_csv(feedback_path)
    assert stats.total_updated == 1
    assert updated.iloc[0]["result"] == "win"
    assert updated.iloc[0]["graded_result"] == "win"
    assert float(updated.iloc[0]["actual_value"]) == 22.0


def test_milestone_rows_remain_unresolved_with_unsupported_reason(tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    feedback_path = _write_feedback(out_dir, [_feedback_row(selection="milestone", line=20.5)])

    stats = backfill_result_feedback(
        runtime_root=out_dir / "runtime",
        out_dir=out_dir,
        write=True,
        ai_factory=_make_ai_factory(FakeBackfillClient()),
    )

    updated = pd.read_csv(feedback_path)
    assert stats.total_unsupported == 1
    assert updated.iloc[0]["result"] == "unresolved"
    assert updated.iloc[0]["graded_result"] == "unresolved"
    assert updated.iloc[0]["ungraded_reason"] == "unsupported_grading_market"


def test_final_win_loss_push_rows_are_preserved(tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    rows = [
        _feedback_row(player="Alpha Player", result="win"),
        _feedback_row(player="Beta Player", team="NYK", selection="under", line=18.5, result="push"),
        _feedback_row(player="Gamma Player", result="loss"),
        _feedback_row(player="Beta Player", team="NYK", selection="under", line=18.5, result="unresolved"),
    ]
    feedback_path = _write_feedback(out_dir, rows)

    backfill_result_feedback(
        runtime_root=out_dir / "runtime",
        out_dir=out_dir,
        write=True,
        ai_factory=_make_ai_factory(FakeBackfillClient()),
    )

    updated = pd.read_csv(feedback_path)
    assert updated.loc[0, "result"] == "win"
    assert updated.loc[1, "result"] == "push"
    assert updated.loc[2, "result"] == "loss"


def test_duplicate_unresolved_grade_keys_are_handled_consistently(tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    row = _feedback_row(selection="over", line=20.5)
    duplicate = dict(row)
    duplicate["confidence"] = 0.63
    feedback_path = _write_feedback(out_dir, [row, duplicate])

    stats = backfill_result_feedback(
        runtime_root=out_dir / "runtime",
        out_dir=out_dir,
        write=True,
        ai_factory=_make_ai_factory(FakeBackfillClient()),
    )

    updated = pd.read_csv(feedback_path)
    assert stats.unresolved_unique_keys == 1
    assert stats.total_updated == 2
    assert updated["result"].tolist() == ["win", "win"]
    assert updated["actual_value"].astype(float).tolist() == [22.0, 22.0]


def test_date_stamped_artifacts_are_regenerated_from_cumulative_final_feedback(tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    date = "2026-04-28"
    rows = [
        _feedback_row(prediction_date=date, player="Alpha Player", selection="over", line=20.5, result="unresolved"),
        _feedback_row(prediction_date=date, player="Beta Player", team="NYK", selection="under", line=18.5, result="loss"),
    ]
    _write_feedback(out_dir, rows)
    paths = _write_grading_outputs(out_dir, date, pd.DataFrame([rows[1]]))
    before_payload = json.loads(paths["grading_summary_json"].read_text(encoding="utf-8"))
    assert before_payload["overall"]["n"] == 1

    backfill_result_feedback(
        runtime_root=out_dir / "runtime",
        out_dir=out_dir,
        write=True,
        ai_factory=_make_ai_factory(FakeBackfillClient()),
    )

    exported = pd.read_csv(paths["grading_results"])
    payload = json.loads(paths["grading_summary_json"].read_text(encoding="utf-8"))
    assert len(exported) == 2
    assert sorted(exported["entity_name"].tolist()) == ["Alpha Player", "Beta Player"]
    assert payload["overall"]["n"] == 2
    assert payload["overall"]["wins"] == 1
    assert payload["overall"]["losses"] == 1
