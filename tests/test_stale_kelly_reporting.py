from __future__ import annotations

from pathlib import Path

import pandas as pd

from courtvision.reporting.quality_summary import build_quality_summary
from scripts.write_daily_summary import write_daily_summary_outputs


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _stale_kelly_row(prediction_date: str) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": "Stale Kelly",
        "market_type": "player_points",
        "selection": "under",
        "line": 27.5,
        "stake_amount": 20.0,
        "expected_value": 2.11,
        "kelly_eligible": True,
        "eligible": True,
        "skip_reason": "",
    }


def _elite_row(prediction_date: str) -> dict:
    return {
        "prediction_date": prediction_date,
        "player_name": "Live Elite",
        "market_type": "player_points",
        "selection": "under",
        "line": 27.5,
        "confidence": 0.75,
        "quality_score": 80.0,
    }


def test_daily_summary_empty_elite_ignores_stale_kelly_file(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    _write_csv(
        operator / f"elite_board_{prediction_date}.csv",
        [],
        columns=["prediction_date", "player_name", "market_type"],
    )
    _write_csv(operator / f"kelly_stakes_{prediction_date}.csv", [_stale_kelly_row(prediction_date)])
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [_elite_row(prediction_date)])

    output_path, metadata = write_daily_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert metadata["elite_count"] == 0
    assert metadata["kelly_eligible_count"] == 0
    assert metadata["total_exposure"] == 0.0
    assert metadata["expected_ev"] == 0.0
    assert "Total exposure: $0.00" in text
    assert "Expected EV: $0.00" in text
    assert "Ignoring Kelly stakes artifact because elite board has 0 rows" in text


def test_quality_summary_empty_elite_ignores_stale_kelly_file(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    _write_csv(
        operator / f"elite_board_{prediction_date}.csv",
        [],
        columns=["prediction_date", "player_name", "market_type"],
    )
    _write_csv(operator / f"kelly_stakes_{prediction_date}.csv", [_stale_kelly_row(prediction_date)])
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [_elite_row(prediction_date)])
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])

    text, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-06T00:00:00+00:00",
    )

    assert payload["candidate_funnel"]["elite_board_count"] == 0
    assert payload["candidate_funnel"]["kelly_rows_count"] == 0
    assert payload["kelly_safety_summary"]["total_rows"] == 0
    assert payload["kelly_safety_summary"]["kelly_eligible_count"] == 0
    assert payload["kelly_safety_summary"]["total_stake"] == 0.0
    assert payload["kelly_safety_summary"]["total_expected_value"] == 0.0
    assert "total Kelly rows: 0" in text
    assert "Ignoring Kelly stakes artifact because elite board has 0 rows" in text


def test_daily_summary_non_empty_elite_preserves_kelly_reporting(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    _write_csv(operator / f"elite_board_{prediction_date}.csv", [_elite_row(prediction_date)])
    _write_csv(operator / f"kelly_stakes_{prediction_date}.csv", [_stale_kelly_row(prediction_date)])
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [_elite_row(prediction_date)])

    output_path, metadata = write_daily_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert metadata["elite_count"] == 1
    assert metadata["kelly_eligible_count"] == 1
    assert metadata["total_exposure"] == 20.0
    assert metadata["expected_ev"] == 2.11
    assert "Total exposure: $20.00" in text
    assert "Expected EV: $2.11" in text


def test_quality_summary_non_empty_elite_preserves_kelly_reporting(tmp_path: Path) -> None:
    prediction_date = "2026-05-06"
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    _write_csv(operator / f"elite_board_{prediction_date}.csv", [_elite_row(prediction_date)])
    _write_csv(operator / f"kelly_stakes_{prediction_date}.csv", [_stale_kelly_row(prediction_date)])
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [_elite_row(prediction_date)])
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])

    _text, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        generated_at="2026-05-06T00:00:00+00:00",
    )

    assert payload["candidate_funnel"]["elite_board_count"] == 1
    assert payload["candidate_funnel"]["kelly_rows_count"] == 1
    assert payload["kelly_safety_summary"]["total_rows"] == 1
    assert payload["kelly_safety_summary"]["kelly_eligible_count"] == 1
    assert payload["kelly_safety_summary"]["total_stake"] == 20.0
    assert payload["kelly_safety_summary"]["total_expected_value"] == 2.11


def test_candidate_scoring_py_is_untouched_by_stale_kelly_reporting() -> None:
    path = Path("courtvision/scoring/candidate_scoring.py")
    content = path.read_text(encoding="utf-8")
    assert "elite_board has 0 rows" not in content
    assert "kelly_df_for_reporting" not in content
