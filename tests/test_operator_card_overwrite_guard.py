from __future__ import annotations

from pathlib import Path

import pytest

from scripts import write_operator_card as operator_card


def test_write_operator_card_no_force_raises_on_existing_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    output_path = runtime_root / "operator" / f"operator_card_{prediction_date}.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("existing card\n", encoding="utf-8")

    def fail_build_operator_card(*_args: object, **_kwargs: object) -> tuple[str, dict[str, object]]:
        raise AssertionError("build_operator_card should not run when overwrite guard blocks")

    monkeypatch.setattr(operator_card, "build_operator_card", fail_build_operator_card)

    with pytest.raises(RuntimeError, match=r"\[ARTIFACT_OVERWRITE_GUARD\]"):
        operator_card.write_operator_card_outputs(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            history_root=history_root,
        )

    assert output_path.read_text(encoding="utf-8") == "existing card\n"


def test_write_operator_card_force_allows_existing_output_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    output_path = runtime_root / "operator" / f"operator_card_{prediction_date}.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("existing card\n", encoding="utf-8")

    def fake_build_operator_card(*_args: object, **_kwargs: object) -> tuple[str, dict[str, object]]:
        return "new card", {"final_decision": "NO BET"}

    monkeypatch.setattr(operator_card, "build_operator_card", fake_build_operator_card)

    path, payload = operator_card.write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        force=True,
    )

    assert path == output_path
    assert payload["final_decision"] == "NO BET"
    assert output_path.read_text(encoding="utf-8") == "new card\n"


def test_write_operator_card_cli_passes_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}
    output_path = tmp_path / "runtime" / "operator" / "operator_card_2026-05-06.txt"

    def fake_write_operator_card_outputs(**kwargs: object) -> tuple[Path, dict[str, object]]:
        captured.update(kwargs)
        return output_path, {"final_decision": "NO BET"}

    monkeypatch.setattr(operator_card, "write_operator_card_outputs", fake_write_operator_card_outputs)

    rc = operator_card.main(
        [
            "--prediction-date",
            "2026-05-06",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--history-root",
            str(tmp_path / "history"),
            "--runtime-mode",
            "research",
            "--force-past-date",
            "true",
            "--force-outputs",
            "false",
            "--kelly-bankroll",
            "2500",
            "--force",
        ]
    )

    output = capsys.readouterr().out
    assert rc == 0
    assert captured["prediction_date"] == "2026-05-06"
    assert captured["runtime_root"] == str(tmp_path / "runtime")
    assert captured["history_root"] == str(tmp_path / "history")
    assert captured["runtime_mode"] == "research"
    assert captured["force_past_date"] == "true"
    assert captured["force_outputs"] == "false"
    assert captured["kelly_bankroll"] == "2500"
    assert captured["force"] is True
    assert f"operator_card_txt={output_path}" in output
    assert "operator_card_decision=NO BET" in output
