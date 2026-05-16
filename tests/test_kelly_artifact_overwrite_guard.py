from pathlib import Path

import pandas as pd
import pytest

from scripts.run_kelly_stakes import main as run_kelly_stakes


def _write_minimal_elite_board(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "player_name": "Test Player",
                "team_abbr": "AAA",
                "opponent": "BBB",
                "market_type": "player_points",
                "selection": "over",
                "line": 10.5,
                "odds": -110,
                "confidence": 0.65,
                "edge": 0.06,
            }
        ]
    ).to_csv(path, index=False)


def test_run_kelly_stakes_does_not_overwrite_existing_output(tmp_path: Path) -> None:
    input_path = tmp_path / "elite_board_2026-05-16.csv"
    output_path = tmp_path / "kelly_stakes_2026-05-16.csv"
    _write_minimal_elite_board(input_path)
    output_path.write_text("sentinel\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="ARTIFACT_OVERWRITE_GUARD"):
        run_kelly_stakes(
            [
                "--prediction-date",
                "2026-05-16",
                "--input-csv",
                str(input_path),
                "--output-csv",
                str(output_path),
            ]
        )

    assert output_path.read_text(encoding="utf-8") == "sentinel\n"


def test_run_kelly_stakes_force_allows_existing_output_overwrite(tmp_path: Path) -> None:
    input_path = tmp_path / "elite_board_2026-05-16.csv"
    output_path = tmp_path / "kelly_stakes_2026-05-16.csv"
    _write_minimal_elite_board(input_path)
    output_path.write_text("sentinel\n", encoding="utf-8")

    rc = run_kelly_stakes(
        [
            "--prediction-date",
            "2026-05-16",
            "--input-csv",
            str(input_path),
            "--output-csv",
            str(output_path),
            "--force",
        ]
    )

    assert rc == 0
    assert "sentinel" not in output_path.read_text(encoding="utf-8")
    assert "Test Player" in output_path.read_text(encoding="utf-8")


def test_run_kelly_stakes_rejects_output_date_mismatch(tmp_path: Path) -> None:
    input_path = tmp_path / "elite_board_2026-05-16.csv"
    output_path = tmp_path / "kelly_stakes_2026-05-17.csv"
    _write_minimal_elite_board(input_path)

    with pytest.raises(RuntimeError, match="ARTIFACT_DATE_GUARD"):
        run_kelly_stakes(
            [
                "--prediction-date",
                "2026-05-16",
                "--input-csv",
                str(input_path),
                "--output-csv",
                str(output_path),
            ]
        )
