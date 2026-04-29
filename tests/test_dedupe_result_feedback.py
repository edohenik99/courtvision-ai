from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.dedupe_result_feedback import ConflictResolution, build_dedupe_plan, parse_conflict_resolution_report


def _row(
    *,
    grade_key: str = "2026-04-28|player_points|Alpha Player|over|20.5",
    result: str = "unresolved",
    actual_value: object = "",
    ungraded_reason: str = "",
) -> dict[str, object]:
    return {
        "grade_key": grade_key,
        "prediction_date": "2026-04-28",
        "market_type": "player_points",
        "entity_name": "Alpha Player",
        "selection": "over",
        "sportsbook_line": 20.5,
        "actual_value": actual_value,
        "result": result,
        "graded_result": result,
        "ungraded_reason": ungraded_reason,
    }


def test_dedupe_prefers_final_result_over_unresolved() -> None:
    deduped, audit = build_dedupe_plan(
        pd.DataFrame(
            [
                _row(result="unresolved", ungraded_reason="player_stats_empty"),
                _row(result="win", actual_value=22.0),
            ]
        )
    )

    assert audit.total_rows == 2
    assert audit.unique_grade_keys == 1
    assert audit.duplicate_grade_keys_count == 1
    assert audit.would_remove_rows == 1
    assert deduped.iloc[0]["result"] == "win"


def test_dedupe_keeps_conflicting_final_rows_for_manual_resolution() -> None:
    deduped, audit = build_dedupe_plan(
        pd.DataFrame(
            [
                _row(result="win", actual_value=22.0),
                _row(result="loss", actual_value=18.0),
                _row(result="unresolved"),
            ]
        )
    )

    assert audit.conflicting_grade_keys_count == 1
    assert audit.conflict_rows == 3
    assert audit.would_remove_rows == 0
    assert len(deduped) == 3
    assert audit.conflicts[0].final_results == ["loss", "win"]


def test_dedupe_applies_reviewed_conflict_resolution() -> None:
    key = "2026-04-28|player_points|Alpha Player|over|20.5"
    deduped, audit = build_dedupe_plan(
        pd.DataFrame(
            [
                _row(grade_key=key, result="win", actual_value=22.0),
                _row(grade_key=key, result="loss", actual_value=18.0),
                _row(grade_key=key, result="unresolved"),
            ]
        ),
        conflict_resolutions={
            key: ConflictResolution(
                grade_key=key,
                actual_value=22.0,
                correct_result="win",
                source="test",
            )
        },
    )

    assert audit.conflicting_grade_keys_count == 0
    assert audit.corrected_conflict_rows == 3
    assert audit.would_keep_rows == 1
    assert audit.would_remove_rows == 2
    assert deduped.iloc[0]["result"] == "win"
    assert float(deduped.iloc[0]["actual_value"]) == 22.0


def test_parse_conflict_resolution_report(tmp_path: Path) -> None:
    path = tmp_path / "resolution.txt"
    path.write_text(
        """
grade_key: 2026-04-28|player_points|Alpha Player|over|20.5
player: Alpha Player
market_type: player_points
selection: over
line: 20.5
actual_value: 22.0
conflicting_results_found: ['loss', 'win']
correct_result: win
source_used: test source
""".strip(),
        encoding="utf-8",
    )

    resolutions = parse_conflict_resolution_report(path)

    assert list(resolutions) == ["2026-04-28|player_points|Alpha Player|over|20.5"]
    assert resolutions["2026-04-28|player_points|Alpha Player|over|20.5"].correct_result == "win"
    assert resolutions["2026-04-28|player_points|Alpha Player|over|20.5"].actual_value == 22.0


def test_dedupe_generates_missing_grade_key_from_existing_project_columns() -> None:
    row = _row(grade_key="", result="push", actual_value=20.5)

    deduped, audit = build_dedupe_plan(pd.DataFrame([row, row]))

    assert audit.blank_grade_key_rows == 2
    assert audit.unique_grade_keys == 1
    assert audit.would_remove_rows == 1
    assert deduped.iloc[0]["grade_key"] == "2026-04-28|player_points|Alpha Player|over|20.5"
