from pathlib import Path

import pandas as pd
import pytest

from courtvision.artifact_guard import guard_no_existing_artifact
from courtvision_ai import _write_prediction_dataframe


def test_guard_no_existing_artifact_passes_if_missing(tmp_path: Path) -> None:
    guard_no_existing_artifact(
        output_path=tmp_path / "elite_board_2026-05-16.csv",
        caller="test",
        artifact_label="elite_board",
    )


def test_guard_no_existing_artifact_raises_if_exists(tmp_path: Path) -> None:
    output_path = tmp_path / "elite_board_2026-05-16.csv"
    output_path.write_text("existing", encoding="utf-8")

    with pytest.raises(RuntimeError, match="ARTIFACT_OVERWRITE_GUARD"):
        guard_no_existing_artifact(
            output_path=output_path,
            caller="test",
            artifact_label="elite_board",
        )


def test_guard_no_existing_artifact_force_bypasses_existing(tmp_path: Path) -> None:
    output_path = tmp_path / "elite_board_2026-05-16.csv"
    output_path.write_text("existing", encoding="utf-8")

    guard_no_existing_artifact(
        output_path=output_path,
        force=True,
        caller="test",
        artifact_label="elite_board",
    )


def test_protected_prediction_dataframe_does_not_overwrite_existing(tmp_path: Path) -> None:
    output_path = tmp_path / "elite_board_2026-05-16.csv"
    output_path.write_text("sentinel\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="ARTIFACT_OVERWRITE_GUARD"):
        _write_prediction_dataframe(
            output_path,
            pd.DataFrame([{"player_name": "Test Player"}]),
            requested_prediction_date="2026-05-16",
            caller="test",
            artifact_label="elite_board",
            protect_existing=True,
        )

    assert output_path.read_text(encoding="utf-8") == "sentinel\n"


def test_protected_prediction_dataframe_force_overwrites_existing(tmp_path: Path) -> None:
    output_path = tmp_path / "elite_board_2026-05-16.csv"
    output_path.write_text("sentinel\n", encoding="utf-8")

    _write_prediction_dataframe(
        output_path,
        pd.DataFrame([{"player_name": "Test Player"}]),
        requested_prediction_date="2026-05-16",
        caller="test",
        artifact_label="elite_board",
        protect_existing=True,
        force_overwrite=True,
    )

    assert "Test Player" in output_path.read_text(encoding="utf-8")
