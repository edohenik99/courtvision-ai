from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

import courtvision_ai


class _Logger:
    def error(self, *args, **kwargs) -> None:
        pass

    def exception(self, *args, **kwargs) -> None:
        pass


class _Runtime:
    def __init__(self, out_dir: str = "outputs") -> None:
        self.out_dir = Path(out_dir)
        self.logger = _Logger()

    def predict(self, prediction_date: str):
        return {"summary": {}, "elite_props": pd.DataFrame()}


def _offline_runtime(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(courtvision_ai, "_load_env_file", lambda: None)
    monkeypatch.setattr(
        courtvision_ai,
        "resolve_api_key",
        lambda **kwargs: (
            "test-key",
            {
                "env_var_name": "BALLDONTLIE_API_KEY",
                "source": "test",
                "masked_preview": "tes***",
            },
        ),
    )
    monkeypatch.setattr(
        courtvision_ai,
        "smoke_test_games_api",
        lambda *args, **kwargs: {
            "status_code": 200,
            "resolved_url": "fixture://games",
            "has_auth": True,
            "masked_key_preview": "tes***",
            "body_snippet": "fixture",
        },
    )
    monkeypatch.setattr(courtvision_ai, "CourtVisionAI", _Runtime)


def test_lifecycle_disabled_preserves_existing_cli_behavior(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _offline_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("COURTVISION_LIFECYCLE_SHADOW", raising=False)
    board = tmp_path / "runtime" / "operator" / "elite_board_2026-07-25.csv"
    board.parent.mkdir(parents=True)
    board.write_text("player_name\n", encoding="utf-8")
    monkeypatch.setattr(
        courtvision_ai,
        "_write_cli_outputs",
        lambda **kwargs: {"elite_board": board},
    )
    rc = courtvision_ai.main(
        [
            "--prediction-date",
            "2026-07-25",
            "--predict-only",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert not (tmp_path / "data" / "lifecycle").exists()


def test_shadow_hook_runs_only_after_successful_actionable_board_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _offline_runtime(monkeypatch, tmp_path)
    calls: list[str] = []
    board = tmp_path / "runtime" / "operator" / "elite_board_2026-07-25.csv"

    class _OrderedRuntime(_Runtime):
        def predict(self, prediction_date: str):
            calls.append("predict")
            return {"summary": {}, "elite_props": pd.DataFrame()}

    monkeypatch.setattr(courtvision_ai, "CourtVisionAI", _OrderedRuntime)

    context = SimpleNamespace(prediction_run_id="run-a", terminal=False)

    def begin(*args, **kwargs):
        calls.append("begin")
        return context

    def write(**kwargs):
        calls.append("board_write")
        board.parent.mkdir(parents=True)
        board.write_text("player_name\n", encoding="utf-8")
        return {"elite_board": board}

    def publish(run, *, board_path):
        calls.append("shadow_publish")
        assert board_path == board
        assert board.is_file()
        run.terminal = True
        return SimpleNamespace(
            status="PASS",
            prediction_run_id="run-a",
            commit_status="COMMITTED",
            message="shadow publication reconciled",
        )

    monkeypatch.setattr(
        courtvision_ai,
        "load_shadow_lifecycle_hooks",
        lambda: SimpleNamespace(
            begin_shadow_run=begin,
            publish_shadow_after_board=publish,
            record_failed_shadow_run=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(courtvision_ai, "_write_cli_outputs", write)
    rc = courtvision_ai.main(
        [
            "--prediction-date",
            "2026-07-25",
            "--predict-only",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert calls == ["begin", "predict", "board_write", "shadow_publish"]


def test_shadow_degraded_does_not_change_successful_canonical_exit_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _offline_runtime(monkeypatch, tmp_path)
    board = tmp_path / "runtime" / "operator" / "elite_board_2026-07-25.csv"
    context = SimpleNamespace(prediction_run_id="run-a", terminal=False)
    monkeypatch.setattr(
        courtvision_ai,
        "load_shadow_lifecycle_hooks",
        lambda: SimpleNamespace(
            begin_shadow_run=lambda *args, **kwargs: context,
            publish_shadow_after_board=lambda *args, **kwargs: SimpleNamespace(
                status="DEGRADED",
                prediction_run_id="run-a",
                commit_status=None,
                message="canonical board published; shadow lifecycle is degraded",
            ),
            record_failed_shadow_run=lambda *args, **kwargs: None,
        ),
    )

    def write(**kwargs):
        board.parent.mkdir(parents=True)
        board.write_text("player_name\n", encoding="utf-8")
        return {"elite_board": board}

    monkeypatch.setattr(courtvision_ai, "_write_cli_outputs", write)
    rc = courtvision_ai.main(
        [
            "--prediction-date",
            "2026-07-25",
            "--predict-only",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
