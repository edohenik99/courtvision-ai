from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from scripts.build_stat_projection_source import (
    OUTPUT_COLUMNS,
    STAT_PROJECTION_INPUT_MISSING,
    STAT_PROJECTION_NO_OUTPUT_ROWS,
    STAT_PROJECTION_OK,
    STAT_PROJECTION_SCHEMA_INVALID,
    build_stat_projection_source,
)


PREDICTION_DATE = "2026-06-05"


def _research_dir(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime" / "research"


def _diagnostics_dir(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime" / "diagnostics"


def _context_path(tmp_path: Path) -> Path:
    return _research_dir(tmp_path) / (
        f"projection_context_clean_{PREDICTION_DATE}.csv"
    )


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "player_id": 101,
        "player_name": "Jane Doe",
        "team_abbr": "OKC",
        "min_avg": 30.0,
        "min_recent": 33.0,
        "pts_avg": 20.0,
        "pts_recent": 24.0,
        "reb_avg": 8.0,
        "reb_recent": 10.0,
        "ast_avg": 6.0,
        "ast_recent": 8.0,
        "eligible_for_betting": False,
    }
    row.update(overrides)
    return row


def _write_context(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = _context_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _run(tmp_path: Path):
    return build_stat_projection_source(
        target_date=PREDICTION_DATE,
        cleaned_context=_context_path(tmp_path),
        output_dir=_research_dir(tmp_path),
        diagnostics_dir=_diagnostics_dir(tmp_path),
    )


def test_creates_stat_projection_source_and_computes_blend(tmp_path: Path) -> None:
    input_path = _write_context(tmp_path, [_row()])

    result = _run(tmp_path)

    assert result.status == STAT_PROJECTION_OK
    output = pd.read_csv(result.output_path)
    assert output.columns.tolist() == OUTPUT_COLUMNS
    assert len(output.index) == 1

    row = output.iloc[0]
    assert row["normalized_player_name"] == "jane doe"
    assert row["minutes_factor"] == pytest.approx(1.1)
    assert row["projected_points"] == pytest.approx(
        (0.65 * 24.0 + 0.35 * 20.0) * 1.1
    )
    assert row["projected_rebounds"] == pytest.approx(
        (0.65 * 10.0 + 0.35 * 8.0) * 1.1
    )
    assert row["projected_assists"] == pytest.approx(
        (0.65 * 8.0 + 0.35 * 6.0) * 1.1
    )
    assert row["projection_method"] == (
        "blended_recent_baseline_minutes_adjusted"
    )
    assert row["projection_quality_flag"] == "research_projection_only"
    assert not bool(row["eligible_for_betting"])

    diagnostics = json.loads(result.diagnostics_path.read_text(encoding="utf-8"))
    assert diagnostics["input_path"] == str(input_path)
    assert diagnostics["output_path"] == str(result.output_path)
    assert diagnostics["input_row_count"] == 1
    assert diagnostics["output_row_count"] == 1
    assert diagnostics["projected_points_available_count"] == 1
    assert diagnostics["projected_rebounds_available_count"] == 1
    assert diagnostics["projected_assists_available_count"] == 1
    assert diagnostics["projection_method_counts"] == {
        "blended_recent_baseline_minutes_adjusted": 1
    }
    assert diagnostics["projection_quality_flag_counts"] == {
        "research_projection_only": 1
    }


def test_clips_minutes_factor_at_both_bounds(tmp_path: Path) -> None:
    _write_context(
        tmp_path,
        [
            _row(
                player_id=1,
                player_name="Upper Clip",
                min_avg=20.0,
                min_recent=30.0,
                pts_avg=10.0,
                pts_recent=10.0,
            ),
            _row(
                player_id=2,
                player_name="Lower Clip",
                min_avg=40.0,
                min_recent=10.0,
                pts_avg=10.0,
                pts_recent=10.0,
            ),
        ],
    )

    result = _run(tmp_path)

    output = pd.read_csv(result.output_path).set_index("player_name")
    assert output.loc["Upper Clip", "minutes_factor"] == pytest.approx(1.15)
    assert output.loc["Upper Clip", "projected_points"] == pytest.approx(11.5)
    assert output.loc["Lower Clip", "minutes_factor"] == pytest.approx(0.85)
    assert output.loc["Lower Clip", "projected_points"] == pytest.approx(8.5)
    assert output.loc["Lower Clip", "projection_quality_flag"] == (
        "low_minutes_context"
    )
    assert result.diagnostics["low_minutes_context_count"] == 1


def test_missing_minutes_uses_unadjusted_blend_and_warning_flag(
    tmp_path: Path,
) -> None:
    _write_context(tmp_path, [_row(min_avg=None, min_recent=None)])

    result = _run(tmp_path)

    output = pd.read_csv(result.output_path)
    row = output.iloc[0]
    assert pd.isna(row["minutes_factor"])
    assert row["projected_points"] == pytest.approx(22.6)
    assert row["projection_method"] == (
        "blended_recent_baseline_no_minutes_adjustment"
    )
    assert row["projection_quality_flag"] == "missing_minutes_context"
    assert result.diagnostics["missing_minutes_context_count"] == 1


def test_missing_stat_context_marks_partial_and_insufficient_rows(
    tmp_path: Path,
) -> None:
    _write_context(
        tmp_path,
        [
            _row(
                player_id=1,
                player_name="Partial Context",
                reb_recent=None,
                ast_avg=None,
                ast_recent=None,
            ),
            _row(
                player_id=2,
                player_name="No Stat Context",
                pts_avg=None,
                pts_recent=None,
                reb_avg=None,
                reb_recent=None,
                ast_avg=None,
                ast_recent=None,
            ),
        ],
    )

    result = _run(tmp_path)

    output = pd.read_csv(result.output_path).set_index("player_name")
    partial = output.loc["Partial Context"]
    assert partial["projected_points"] == pytest.approx(24.86)
    assert pd.isna(partial["projected_rebounds"])
    assert pd.isna(partial["projected_assists"])
    assert partial["projection_method"] == (
        "blended_recent_baseline_minutes_adjusted"
    )
    assert partial["projection_quality_flag"] == "missing_stat_context"

    insufficient = output.loc["No Stat Context"]
    assert pd.isna(insufficient["projected_points"])
    assert insufficient["projection_method"] == "insufficient_data"
    assert insufficient["projection_quality_flag"] == "missing_stat_context"
    assert result.diagnostics["insufficient_data_count"] == 1
    assert result.diagnostics["projection_quality_flag_counts"] == {
        "missing_stat_context": 2
    }


def test_eligible_for_betting_is_always_false(tmp_path: Path) -> None:
    _write_context(tmp_path, [_row(eligible_for_betting=True)])

    result = _run(tmp_path)

    output = pd.read_csv(result.output_path)
    assert output["eligible_for_betting"].tolist() == [False]
    assert result.diagnostics["eligible_for_betting_any_true"] is False
    assert any(
        "Ignored truthy eligible_for_betting" in warning
        for warning in result.diagnostics["warnings"]
    )


def test_no_betting_artifacts_are_written(tmp_path: Path) -> None:
    operator_dir = tmp_path / "outputs" / "runtime" / "operator"
    operator_dir.mkdir(parents=True)
    kelly_path = operator_dir / f"kelly_stakes_{PREDICTION_DATE}.csv"
    elite_path = operator_dir / f"elite_board_{PREDICTION_DATE}.csv"
    kelly_contents = "player_name,stake\nExisting,10\n"
    elite_contents = "player_name,score\nExisting,99\n"
    kelly_path.write_text(kelly_contents, encoding="utf-8")
    elite_path.write_text(elite_contents, encoding="utf-8")
    _write_context(tmp_path, [_row()])

    result = _run(tmp_path)

    assert result.diagnostics["market_prop_rows_created"] == 0
    assert result.diagnostics["elite_rows_created"] == 0
    assert result.diagnostics["kelly_called"] is False
    assert result.diagnostics["operator_betting_boards_written"] == []
    assert kelly_path.read_text(encoding="utf-8") == kelly_contents
    assert elite_path.read_text(encoding="utf-8") == elite_contents
    assert not (
        operator_dir / f"full_market_board_{PREDICTION_DATE}.csv"
    ).exists()


def test_non_success_statuses_still_write_empty_projection_artifacts(
    tmp_path: Path,
) -> None:
    missing = _run(tmp_path)
    assert missing.status == STAT_PROJECTION_INPUT_MISSING
    assert pd.read_csv(missing.output_path).empty

    pd.DataFrame([{"team_abbr": "OKC"}]).to_csv(
        _context_path(tmp_path),
        index=False,
    )
    invalid = _run(tmp_path)
    assert invalid.status == STAT_PROJECTION_SCHEMA_INVALID
    assert pd.read_csv(invalid.output_path).empty

    pd.DataFrame(columns=["player_name", "pts_avg", "pts_recent"]).to_csv(
        _context_path(tmp_path),
        index=False,
    )
    no_rows = _run(tmp_path)
    assert no_rows.status == STAT_PROJECTION_NO_OUTPUT_ROWS
    assert pd.read_csv(no_rows.output_path).empty
