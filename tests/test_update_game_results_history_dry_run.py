from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from courtvision.ratings.power_ratings_store import GAME_RESULTS_COLUMNS, append_game_results
from scripts import update_game_results_history as game_history


def _game_result_row(game_id: str, date: str = "2026-05-06") -> dict[str, object]:
    return {
        "date": date,
        "home_team_id": "BOS",
        "away_team_id": "NYK",
        "home_score": 110,
        "away_score": 100,
        "game_id": game_id,
        "home_team_name": "Boston Celtics",
        "away_team_name": "New York Knicks",
    }


def test_append_game_results_dry_run_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "history" / "game_results.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = pd.DataFrame([_game_result_row("existing")], columns=list(GAME_RESULTS_COLUMNS))
    existing.to_csv(path, index=False)
    before = path.read_text(encoding="utf-8")

    incoming = pd.DataFrame([_game_result_row("new")], columns=list(GAME_RESULTS_COLUMNS))
    summary = append_game_results(incoming, path=path, dry_run=True)

    assert summary["dry_run"] is True
    assert summary["accepted"] == 1
    assert summary["appended"] == 1
    assert summary["total_rows"] == 2
    assert path.read_text(encoding="utf-8") == before


def test_update_game_results_history_cli_passes_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def get_games(self, game_date: str) -> pd.DataFrame:
            captured["game_date"] = game_date
            return pd.DataFrame([{"id": "raw-game"}])

    class FakeCourtVisionAI:
        def __init__(self, out_dir: str) -> None:
            captured["out_dir"] = out_dir

        def _get_client(self) -> FakeClient:
            return FakeClient()

    def fake_games_df_to_results(raw_games_df: pd.DataFrame, fallback_date: str = "") -> pd.DataFrame:
        captured["fallback_date"] = fallback_date
        return pd.DataFrame([_game_result_row("new", date=fallback_date)], columns=list(GAME_RESULTS_COLUMNS))

    def fake_append_game_results(
        new_df: pd.DataFrame,
        path: str | Path | None = None,
        dry_run: bool = False,
    ) -> dict[str, object]:
        captured["append_rows"] = len(new_df)
        captured["append_path"] = path
        captured["dry_run"] = dry_run
        return {
            "fetched": len(new_df),
            "accepted": len(new_df),
            "appended": len(new_df),
            "skipped_duplicates": 0,
            "total_rows": len(new_df),
            "output_path": str(path),
            "dry_run": dry_run,
        }

    monkeypatch.setattr(game_history, "CourtVisionAI", FakeCourtVisionAI)
    monkeypatch.setattr(game_history, "games_df_to_results", fake_games_df_to_results)
    monkeypatch.setattr(game_history, "append_game_results", fake_append_game_results)

    rc = game_history.main(
        [
            "--date",
            "2026-05-06",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--history-root",
            str(tmp_path / "history"),
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert rc == 0
    assert captured["out_dir"] == str(tmp_path / "runtime")
    assert captured["game_date"] == "2026-05-06"
    assert captured["fallback_date"] == "2026-05-06"
    assert captured["append_rows"] == 1
    assert captured["append_path"] == tmp_path / "history" / "game_results.csv"
    assert captured["dry_run"] is True
    assert "dry_run:                 true" in output
