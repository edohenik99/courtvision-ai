"""Read-only temporal split planning for MLB HR research backtests.

This module can propose date windows after the historical pack preflight and
readiness audit pass.  It never builds features, trains a model, makes a
prediction, runs a backtest, or writes an artifact.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Final, Iterable

from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessVerdict,
    audit_historical_backtest_readiness,
)
from courtvision.sports.mlb.data.historical_input_pack import (
    historical_input_pack_paths,
    preflight_historical_input_pack,
)
from courtvision.sports.mlb.training.hr_feature_allowlist import (
    MLBHRResearchFeaturePack,
    validate_mlb_hr_feature_pack,
)


TRAIN_DATE_NUMERATOR: Final = 3
VALIDATION_DATE_NUMERATOR: Final = 1
SPLIT_DATE_DENOMINATOR: Final = 5

# The readiness audit requires at least 30 unique game dates.  These floors
# preserve a deterministic 60/20/20 allocation at that minimum.
MIN_TRAIN_UNIQUE_DATES: Final = 18
MIN_VALIDATION_UNIQUE_DATES: Final = 6
MIN_TEST_UNIQUE_DATES: Final = 6


class TemporalBacktestPlanningError(ValueError):
    """Raised when a safe temporal split cannot be proposed."""


@dataclass(frozen=True, slots=True)
class TemporalDateWindow:
    """One contiguous, whole-date partition in a proposed research split."""

    name: str
    game_dates: tuple[date, ...]

    def __post_init__(self) -> None:
        normalized_dates = tuple(sorted(set(self.game_dates)))
        if not normalized_dates:
            raise TemporalBacktestPlanningError(
                f"{self.name} split must contain at least one game date"
            )
        object.__setattr__(self, "game_dates", normalized_dates)

    @property
    def start(self) -> date:
        return self.game_dates[0]

    @property
    def end(self) -> date:
        return self.game_dates[-1]

    @property
    def unique_date_count(self) -> int:
        return len(self.game_dates)


@dataclass(frozen=True, slots=True)
class TemporalSplitPlan:
    """Immutable split proposal; this is not permission to execute a backtest."""

    pack_dir: Path
    train: TemporalDateWindow
    validation: TemporalDateWindow
    test: TemporalDateWindow
    split_method: str = "whole_unique_game_dates_60_20_20"
    readiness_verdict: str = (
        HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
    )
    approval_status: str = "not_approved"
    model_training_enabled: bool = False
    backtesting_enabled: bool = False
    predictions_enabled: bool = False
    eligible_for_betting: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "pack_dir", self.pack_dir.resolve())
        if self.readiness_verdict != (
            HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
        ):
            raise TemporalBacktestPlanningError(
                "a temporal plan requires READY_FOR_RESEARCH_BACKTEST"
            )
        if self.approval_status != "not_approved":
            raise TemporalBacktestPlanningError(
                "a research split plan cannot grant production approval"
            )
        if any(
            (
                self.model_training_enabled,
                self.backtesting_enabled,
                self.predictions_enabled,
                self.eligible_for_betting,
            )
        ):
            raise TemporalBacktestPlanningError(
                "a research split plan cannot enable execution gates"
            )
        if not self.train.end < self.validation.start < self.test.start:
            raise TemporalBacktestPlanningError(
                "train, validation, and test dates must be strictly ordered"
            )
        assigned_dates = (
            self.train.game_dates
            + self.validation.game_dates
            + self.test.game_dates
        )
        if len(assigned_dates) != len(set(assigned_dates)):
            raise TemporalBacktestPlanningError(
                "a game date cannot appear in more than one split"
            )


@dataclass(frozen=True, slots=True)
class HistoricalBacktestDryRunResult:
    """Default-deny result from preflight, readiness, and split planning."""

    pack_dir: Path
    preflight_valid: bool
    readiness_verdict: str
    split_plan: TemporalSplitPlan | None = None
    preflight_errors: tuple[str, ...] = field(default_factory=tuple)
    refusal_reasons: tuple[str, ...] = field(default_factory=tuple)
    feature_firewall_checked: bool = False
    feature_firewall_valid: bool = False
    feature_firewall_errors: tuple[str, ...] = field(default_factory=tuple)
    approval_status: str = "not_approved"
    model_training_enabled: bool = False
    backtesting_enabled: bool = False
    predictions_enabled: bool = False
    eligible_for_betting: bool = False
    ev_enabled: bool = False
    kelly_eligible: bool = False
    elite_enabled: bool = False
    staking_enabled: bool = False
    artifacts_written: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "pack_dir", self.pack_dir.resolve())
        object.__setattr__(self, "preflight_errors", tuple(self.preflight_errors))
        object.__setattr__(self, "refusal_reasons", tuple(self.refusal_reasons))
        object.__setattr__(
            self, "feature_firewall_errors", tuple(self.feature_firewall_errors)
        )
        if self.feature_firewall_valid and not self.feature_firewall_checked:
            raise TemporalBacktestPlanningError(
                "an unchecked feature firewall cannot be marked valid"
            )
        if self.feature_firewall_valid and self.feature_firewall_errors:
            raise TemporalBacktestPlanningError(
                "a valid feature firewall cannot contain errors"
            )
        if self.approval_status != "not_approved":
            raise TemporalBacktestPlanningError(
                "dry-run planning cannot grant production approval"
            )
        if any(
            (
                self.model_training_enabled,
                self.backtesting_enabled,
                self.predictions_enabled,
                self.eligible_for_betting,
                self.ev_enabled,
                self.kelly_eligible,
                self.elite_enabled,
                self.staking_enabled,
                self.artifacts_written,
            )
        ):
            raise TemporalBacktestPlanningError(
                "dry-run planning cannot enable execution or write artifacts"
            )
        if self.split_plan is not None and self.refusal_reasons:
            raise TemporalBacktestPlanningError(
                "a refused dry run cannot contain a split plan"
            )

    @property
    def split_planned(self) -> bool:
        return self.split_plan is not None


def plan_temporal_date_splits(
    game_dates: Iterable[date],
    *,
    pack_dir: str | Path,
) -> TemporalSplitPlan:
    """Allocate whole game dates into deterministic 60/20/20 windows."""

    ordered_dates = tuple(sorted(set(game_dates)))
    minimum_total = (
        MIN_TRAIN_UNIQUE_DATES
        + MIN_VALIDATION_UNIQUE_DATES
        + MIN_TEST_UNIQUE_DATES
    )
    if len(ordered_dates) < minimum_total:
        raise TemporalBacktestPlanningError(
            f"split planning requires at least {minimum_total} unique game dates; "
            f"found {len(ordered_dates)}"
        )

    train_count = (
        len(ordered_dates) * TRAIN_DATE_NUMERATOR // SPLIT_DATE_DENOMINATOR
    )
    validation_count = (
        len(ordered_dates) * VALIDATION_DATE_NUMERATOR // SPLIT_DATE_DENOMINATOR
    )
    test_count = len(ordered_dates) - train_count - validation_count
    if (
        train_count < MIN_TRAIN_UNIQUE_DATES
        or validation_count < MIN_VALIDATION_UNIQUE_DATES
        or test_count < MIN_TEST_UNIQUE_DATES
    ):
        raise TemporalBacktestPlanningError(
            "60/20/20 split does not meet the 18/6/6 unique-date floors"
        )

    validation_start = train_count
    test_start = train_count + validation_count
    return TemporalSplitPlan(
        pack_dir=Path(pack_dir).expanduser(),
        train=TemporalDateWindow("train", ordered_dates[:validation_start]),
        validation=TemporalDateWindow(
            "validation", ordered_dates[validation_start:test_start]
        ),
        test=TemporalDateWindow("test", ordered_dates[test_start:]),
    )


def _read_unique_game_dates(path: Path) -> tuple[date, ...]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if "game_date" not in (reader.fieldnames or ()):
                raise TemporalBacktestPlanningError(
                    f"{path.name} is missing required game_date column"
                )
            parsed_dates: set[date] = set()
            for row_number, row in enumerate(reader, start=2):
                raw_value = str(row.get("game_date") or "").strip()
                try:
                    parsed_dates.add(date.fromisoformat(raw_value))
                except ValueError as exc:
                    raise TemporalBacktestPlanningError(
                        f"{path.name} row {row_number} has invalid game_date: "
                        f"{raw_value!r}"
                    ) from exc
    except (OSError, UnicodeError, csv.Error) as exc:
        raise TemporalBacktestPlanningError(
            f"could not read split dates from {path.name}: {exc}"
        ) from exc
    return tuple(sorted(parsed_dates))


def dry_run_historical_research_backtest(
    pack_dir: str | Path,
    *,
    feature_pack: MLBHRResearchFeaturePack | None = None,
) -> HistoricalBacktestDryRunResult:
    """Run read-only gates and, only when ready, propose temporal windows.

    When a proposed feature pack is supplied, its exact column allowlist and
    per-value timestamp lineage must pass before split dates are read.
    """

    paths = historical_input_pack_paths(pack_dir)
    preflight = preflight_historical_input_pack(paths.root)
    readiness = audit_historical_backtest_readiness(paths.root)
    required_verdict = (
        HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
    )
    feature_firewall = (
        validate_mlb_hr_feature_pack(feature_pack)
        if feature_pack is not None
        else None
    )

    refusal_reasons: list[str] = []
    if not preflight.is_valid:
        refusal_reasons.append("historical input-pack preflight did not pass")
    if not readiness.preflight_valid:
        refusal_reasons.append("readiness audit reports an invalid input pack")
    if readiness.verdict != required_verdict:
        refusal_reasons.append(
            f"readiness verdict must be {required_verdict}; "
            f"found {readiness.verdict}"
        )
    if readiness.possible_leakage_columns:
        refusal_reasons.append(
            "possible leakage columns must be removed before split planning: "
            + ", ".join(readiness.possible_leakage_columns)
        )
    if feature_firewall is not None and not feature_firewall.is_valid:
        refusal_reasons.append(
            "feature leakage firewall rejected pack: "
            + "; ".join(feature_firewall.errors)
        )
    refusal_reasons = list(dict.fromkeys(refusal_reasons))
    if refusal_reasons:
        return HistoricalBacktestDryRunResult(
            pack_dir=paths.root,
            preflight_valid=preflight.is_valid,
            readiness_verdict=readiness.verdict,
            preflight_errors=preflight.errors,
            refusal_reasons=tuple(refusal_reasons),
            feature_firewall_checked=feature_firewall is not None,
            feature_firewall_valid=(
                feature_firewall.is_valid if feature_firewall is not None else False
            ),
            feature_firewall_errors=(
                feature_firewall.errors if feature_firewall is not None else ()
            ),
        )

    try:
        game_dates = _read_unique_game_dates(paths.retrosheet_games)
        if len(game_dates) != readiness.unique_dates:
            raise TemporalBacktestPlanningError(
                "readiness unique-date count changed before split planning"
            )
        if (
            not game_dates
            or game_dates[0] != readiness.date_range_start
            or game_dates[-1] != readiness.date_range_end
        ):
            raise TemporalBacktestPlanningError(
                "readiness date range changed before split planning"
            )
        split_plan = plan_temporal_date_splits(
            game_dates,
            pack_dir=paths.root,
        )
    except TemporalBacktestPlanningError as exc:
        return HistoricalBacktestDryRunResult(
            pack_dir=paths.root,
            preflight_valid=preflight.is_valid,
            readiness_verdict=readiness.verdict,
            preflight_errors=preflight.errors,
            refusal_reasons=(str(exc),),
            feature_firewall_checked=feature_firewall is not None,
            feature_firewall_valid=(
                feature_firewall.is_valid if feature_firewall is not None else False
            ),
            feature_firewall_errors=(
                feature_firewall.errors if feature_firewall is not None else ()
            ),
        )

    return HistoricalBacktestDryRunResult(
        pack_dir=paths.root,
        preflight_valid=preflight.is_valid,
        readiness_verdict=readiness.verdict,
        split_plan=split_plan,
        preflight_errors=preflight.errors,
        feature_firewall_checked=feature_firewall is not None,
        feature_firewall_valid=(
            feature_firewall.is_valid if feature_firewall is not None else False
        ),
        feature_firewall_errors=(
            feature_firewall.errors if feature_firewall is not None else ()
        ),
    )


__all__ = [
    "MIN_TEST_UNIQUE_DATES",
    "MIN_TRAIN_UNIQUE_DATES",
    "MIN_VALIDATION_UNIQUE_DATES",
    "HistoricalBacktestDryRunResult",
    "TemporalBacktestPlanningError",
    "TemporalDateWindow",
    "TemporalSplitPlan",
    "dry_run_historical_research_backtest",
    "plan_temporal_date_splits",
]
