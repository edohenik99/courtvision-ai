from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import shutil
from typing import Callable, Mapping, Sequence

import pytest

from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessVerdict,
    audit_historical_backtest_readiness,
)
from courtvision.sports.mlb.data.historical_input_pack import PACK_SOURCE_FILES
import scripts.mlb_audit_hr_backtest_readiness as readiness_cli


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mlb" / "real_aligned_pack"


def _copy_pack(tmp_path: Path) -> Path:
    pack_dir = tmp_path / "candidate_pack"
    shutil.copytree(FIXTURE_DIR, pack_dir)
    return pack_dir


def _rewrite_source(
    pack_dir: Path,
    source_name: str,
    transform: Callable[
        [list[str], list[dict[str, str]]],
        tuple[Sequence[str], Sequence[Mapping[str, object]]],
    ],
) -> None:
    source_path = pack_dir / PACK_SOURCE_FILES[source_name]
    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or ())
        rows = [dict(row) for row in reader]
    rewritten_headers, rewritten_rows = transform(headers, rows)
    with source_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rewritten_headers))
        writer.writeheader()
        writer.writerows(rewritten_rows)

    manifest_path = pack_dir / "input_pack_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_entry = next(
        entry for entry in manifest["sources"] if entry["source_name"] == source_name
    )
    source_bytes = source_path.read_bytes()
    source_entry["sha256"] = hashlib.sha256(source_bytes).hexdigest()
    source_entry["byte_size"] = len(source_bytes)
    source_entry["parsed_row_count"] = len(rewritten_rows)
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def _snapshot_tree(root: Path) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        sorted(
            (
                str(path.relative_to(root)),
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_size,
            )
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def test_tiny_pack_returns_not_ready(tmp_path: Path) -> None:
    pack_dir = _copy_pack(tmp_path)

    _rewrite_source(
        pack_dir,
        "statcast",
        lambda headers, rows: (
            headers,
            [row for row in rows if row["batter"] == "592450"],
        ),
    )
    _rewrite_source(
        pack_dir,
        "retrosheet_events",
        lambda headers, rows: (
            headers,
            [row for row in rows if row["batter_id"] == "592450"],
        ),
    )
    _rewrite_source(
        pack_dir,
        "odds_snapshot",
        lambda headers, rows: (
            headers,
            [row for row in rows if row["player_id"] == "592450"],
        ),
    )

    report = audit_historical_backtest_readiness(pack_dir)

    assert report.verdict == HistoricalBacktestReadinessVerdict.NOT_READY.value
    assert report.labeled_player_game_rows == 1
    assert any("review requires >= 2" in reason for reason in report.blocking_reasons)


def test_valid_minimum_pack_returns_ready_for_review(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pack_dir = _copy_pack(tmp_path)

    report = audit_historical_backtest_readiness(pack_dir)

    assert report.preflight_valid
    assert report.verdict == HistoricalBacktestReadinessVerdict.READY_FOR_REVIEW.value
    assert report.labeled_player_game_rows == 2
    assert report.unique_games == 1
    assert report.unique_players == 2
    assert report.hr_positive_count == 1
    assert report.hr_negative_count == 1
    assert report.odds_coverage_rate == 1.0
    assert report.weather_coverage_rate == 1.0
    assert report.ballpark_coverage_rate == 1.0
    assert report.blocking_reasons == ()
    assert report.research_review_items

    assert readiness_cli.main([str(pack_dir)]) == 0
    output = capsys.readouterr().out
    assert "verdict: READY_FOR_REVIEW" in output
    assert "backtesting_enabled: false" in output


def test_possible_leakage_column_fails(tmp_path: Path) -> None:
    pack_dir = _copy_pack(tmp_path)

    def add_leakage_column(
        headers: list[str], rows: list[dict[str, str]]
    ) -> tuple[list[str], list[dict[str, str]]]:
        headers.append("hit_hr_today")
        for row in rows:
            row["hit_hr_today"] = "true"
        return headers, rows

    _rewrite_source(pack_dir, "statcast", add_leakage_column)

    report = audit_historical_backtest_readiness(pack_dir)

    assert report.verdict == HistoricalBacktestReadinessVerdict.NOT_READY.value
    assert report.possible_leakage_columns == ("statcast.hit_hr_today",)
    assert any(
        "possible leakage columns" in reason
        for reason in report.blocking_reasons
    )


def test_missing_labels_fail_even_when_event_type_can_be_inferred(
    tmp_path: Path,
) -> None:
    pack_dir = _copy_pack(tmp_path)

    def remove_label(
        headers: list[str], rows: list[dict[str, str]]
    ) -> tuple[list[str], list[dict[str, str]]]:
        rows[0]["is_home_run"] = ""
        return headers, rows

    _rewrite_source(pack_dir, "retrosheet_events", remove_label)

    report = audit_historical_backtest_readiness(pack_dir)

    assert report.verdict == HistoricalBacktestReadinessVerdict.NOT_READY.value
    assert report.missing_label_count == 1
    assert any(
        "outcome labels are missing" in reason
        for reason in report.blocking_reasons
    )


def test_duplicate_player_game_rows_fail(tmp_path: Path) -> None:
    pack_dir = _copy_pack(tmp_path)

    def duplicate_label(
        headers: list[str], rows: list[dict[str, str]]
    ) -> tuple[list[str], list[dict[str, str]]]:
        return headers, [*rows, dict(rows[0])]

    _rewrite_source(pack_dir, "retrosheet_events", duplicate_label)

    report = audit_historical_backtest_readiness(pack_dir)

    assert report.verdict == HistoricalBacktestReadinessVerdict.NOT_READY.value
    assert report.duplicate_player_game_rows == 1
    assert any(
        "duplicate player-game" in reason for reason in report.blocking_reasons
    )


def test_sample_identity_is_rejected(tmp_path: Path) -> None:
    pack_dir = _copy_pack(tmp_path)

    def make_sample_identity(
        headers: list[str], rows: list[dict[str, str]]
    ) -> tuple[list[str], list[dict[str, str]]]:
        rows[0]["batter_name"] = "Sample Slugger"
        return headers, rows

    _rewrite_source(pack_dir, "retrosheet_events", make_sample_identity)

    report = audit_historical_backtest_readiness(pack_dir)

    assert report.verdict == HistoricalBacktestReadinessVerdict.NOT_READY.value
    assert report.synthetic_identity_findings
    assert any("sample/synthetic" in reason for reason in report.blocking_reasons)


def test_cli_does_not_mutate_pack_or_operational_folders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack_dir = _copy_pack(tmp_path)
    restricted_roots = []
    for folder_name in ("outputs", "history", "runtime", "manual-data", "cache"):
        restricted = tmp_path / folder_name
        restricted.mkdir()
        (restricted / "sentinel.txt").write_text(
            f"preserve {folder_name}", encoding="utf-8"
        )
        restricted_roots.append(restricted)
    before_pack = _snapshot_tree(pack_dir)
    before_restricted = {
        root.name: _snapshot_tree(root) for root in restricted_roots
    }
    monkeypatch.chdir(tmp_path)

    assert readiness_cli.main([str(pack_dir), "--format", "json"]) == 0

    assert _snapshot_tree(pack_dir) == before_pack
    assert {
        root.name: _snapshot_tree(root) for root in restricted_roots
    } == before_restricted
