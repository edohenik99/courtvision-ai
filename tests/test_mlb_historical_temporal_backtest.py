from __future__ import annotations

import csv
from datetime import date, timedelta
import hashlib
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessVerdict,
)
import courtvision.sports.mlb.data.historical_temporal_backtest as temporal


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mlb" / "real_aligned_pack"


def _copy_pack(tmp_path: Path) -> Path:
    pack_dir = tmp_path / "candidate_pack"
    shutil.copytree(FIXTURE_DIR, pack_dir)
    return pack_dir


def _write_split_dates(pack_dir: Path, game_dates: list[date]) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    with (pack_dir / "retrosheet_games.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=("game_id", "game_date"))
        writer.writeheader()
        for index, game_date in enumerate(game_dates, start=1):
            writer.writerow(
                {"game_id": str(800_000 + index), "game_date": game_date}
            )


def _stub_ready_gates(
    monkeypatch: pytest.MonkeyPatch,
    game_dates: list[date],
) -> list[str]:
    calls: list[str] = []

    def preflight(_pack_dir: Path) -> SimpleNamespace:
        calls.append("preflight")
        return SimpleNamespace(is_valid=True, errors=())

    def audit(_pack_dir: Path) -> SimpleNamespace:
        calls.append("readiness")
        return SimpleNamespace(
            preflight_valid=True,
            verdict=(
                HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
            ),
            possible_leakage_columns=(),
            unique_dates=len(set(game_dates)),
            date_range_start=min(game_dates),
            date_range_end=max(game_dates),
        )

    monkeypatch.setattr(temporal, "preflight_historical_input_pack", preflight)
    monkeypatch.setattr(temporal, "audit_historical_backtest_readiness", audit)
    return calls


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


def test_not_ready_pack_is_rejected_before_split_planning(tmp_path: Path) -> None:
    pack_dir = tmp_path / "incomplete_pack"
    pack_dir.mkdir()

    result = temporal.dry_run_historical_research_backtest(pack_dir)

    assert not result.split_planned
    assert result.readiness_verdict == (
        HistoricalBacktestReadinessVerdict.NOT_READY.value
    )
    assert any(
        "READY_FOR_RESEARCH_BACKTEST" in reason
        for reason in result.refusal_reasons
    )


def test_ready_for_review_pack_is_rejected(
    tmp_path: Path,
) -> None:
    pack_dir = _copy_pack(tmp_path)

    result = temporal.dry_run_historical_research_backtest(pack_dir)

    assert result.readiness_verdict == (
        HistoricalBacktestReadinessVerdict.READY_FOR_REVIEW.value
    )
    assert not result.split_planned
    assert result.split_plan is None


def test_ready_for_research_backtest_reaches_dry_run_split_planning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack_dir = tmp_path / "ready_pack"
    game_dates = [date(2024, 4, 1) + timedelta(days=index) for index in range(30)]
    _write_split_dates(pack_dir, game_dates)
    calls = _stub_ready_gates(monkeypatch, game_dates)

    result = temporal.dry_run_historical_research_backtest(pack_dir)

    assert calls == ["preflight", "readiness"]
    assert result.readiness_verdict == (
        HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
    )
    assert result.split_planned
    assert result.split_plan is not None
    assert result.split_plan.train.start == date(2024, 4, 1)
    assert result.split_plan.train.end == date(2024, 4, 18)
    assert result.split_plan.validation.start == date(2024, 4, 19)
    assert result.split_plan.validation.end == date(2024, 4, 24)
    assert result.split_plan.test.start == date(2024, 4, 25)
    assert result.split_plan.test.end == date(2024, 4, 30)
    assert not result.model_training_enabled
    assert not result.backtesting_enabled
    assert not result.artifacts_written


def test_split_dates_are_strictly_ordered_and_never_shared(
    tmp_path: Path,
) -> None:
    ordered_dates = [
        date(2024, 5, 1) + timedelta(days=index) for index in range(30)
    ]
    unsorted_with_duplicate = [*reversed(ordered_dates), ordered_dates[0]]

    plan = temporal.plan_temporal_date_splits(
        unsorted_with_duplicate,
        pack_dir=tmp_path,
    )

    assert plan.train.unique_date_count == 18
    assert plan.validation.unique_date_count == 6
    assert plan.test.unique_date_count == 6
    assert plan.train.end < plan.validation.start
    assert plan.validation.end < plan.test.start
    assigned_dates = (
        plan.train.game_dates
        + plan.validation.game_dates
        + plan.test.game_dates
    )
    assert len(assigned_dates) == len(set(assigned_dates)) == 30
    assert set(assigned_dates) == set(ordered_dates)


def test_leakage_columns_fail_before_split_date_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack_dir = tmp_path / "leaking_pack"
    pack_dir.mkdir()

    monkeypatch.setattr(
        temporal,
        "preflight_historical_input_pack",
        lambda _pack_dir: SimpleNamespace(is_valid=True, errors=()),
    )
    monkeypatch.setattr(
        temporal,
        "audit_historical_backtest_readiness",
        lambda _pack_dir: SimpleNamespace(
            preflight_valid=True,
            verdict=(
                HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
            ),
            possible_leakage_columns=("statcast.hit_hr_today",),
        ),
    )

    def fail_if_split_dates_are_read(_path: Path) -> tuple[date, ...]:
        raise AssertionError("split planning must not read dates after leakage")

    monkeypatch.setattr(
        temporal, "_read_unique_game_dates", fail_if_split_dates_are_read
    )

    result = temporal.dry_run_historical_research_backtest(pack_dir)

    assert not result.split_planned
    assert result.refusal_reasons == (
        "possible leakage columns must be removed before split planning: "
        "statcast.hit_hr_today",
    )


def test_successful_dry_run_does_not_mutate_pack_or_operational_folders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack_dir = tmp_path / "ready_pack"
    game_dates = [date(2024, 6, 1) + timedelta(days=index) for index in range(30)]
    _write_split_dates(pack_dir, game_dates)
    _stub_ready_gates(monkeypatch, game_dates)

    restricted_roots = []
    for folder_name in (
        "output",
        "outputs",
        "history",
        "runtime",
        "manual-data",
    ):
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

    result = temporal.dry_run_historical_research_backtest(pack_dir)

    assert result.split_planned
    assert _snapshot_tree(pack_dir) == before_pack
    assert {
        root.name: _snapshot_tree(root) for root in restricted_roots
    } == before_restricted
