"""Fail-closed feature allowlist for MLB HR research backtests.

This module validates only feature names and timestamp lineage.  It does not
build features, train a model, execute a backtest, make predictions, or grant
production or wagering approval.  Raw outcome tables may retain labels for
later evaluation; those fields are rejected when declared as features.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
import re
from typing import Final, Iterable

from courtvision.sports.mlb.training.hr_dataset_schema import (
    HISTORICAL_ROLLING_FEATURE_FIELD_NAMES,
    ODDS_CONTEXT_FIELD_NAMES,
    OUTCOME_LABEL_FIELD_NAMES,
    PREGAME_CONTEXT_FEATURE_FIELD_NAMES,
)


class MLBHRFeatureFirewallError(ValueError):
    """Raised when a research feature declaration fails closed."""


class MLBHRFeatureFieldClass(StrEnum):
    """The five explicit field classes at the research-backtest boundary."""

    ALLOWED_PREGAME = "allowed_pregame_features"
    ALLOWED_HISTORICAL_ROLLING = "allowed_historical_rolling_features"
    ALLOWED_MARKET = "allowed_market_features"
    LABEL_OUTCOME = "labels_outcomes"
    FORBIDDEN_LEAKAGE = "forbidden_leakage_fields"


ALLOWED_PREGAME_FEATURES: Final = frozenset(
    PREGAME_CONTEXT_FEATURE_FIELD_NAMES
)
ALLOWED_HISTORICAL_ROLLING_FEATURES: Final = frozenset(
    HISTORICAL_ROLLING_FEATURE_FIELD_NAMES
)
ALLOWED_MARKET_FEATURES: Final = frozenset(ODDS_CONTEXT_FIELD_NAMES)
ALLOWED_MLB_HR_RESEARCH_FEATURES: Final = frozenset(
    {
        *ALLOWED_PREGAME_FEATURES,
        *ALLOWED_HISTORICAL_ROLLING_FEATURES,
        *ALLOWED_MARKET_FEATURES,
    }
)

# These fields may exist in label/result sources, but never in a feature set.
LABEL_OUTCOME_FIELDS: Final = frozenset(
    {
        *OUTCOME_LABEL_FIELD_NAMES,
        "description",
        "event_text",
        "event_type",
        "events",
        "is_home_run",
        "label",
        "outcome",
        "rbi",
        "result",
        "target",
    }
)

# Known postgame, final-result, decision-output, and post-start market fields.
# Pattern checks below extend this list without ever allowing an unknown name.
FORBIDDEN_LEAKAGE_FIELDS: Final = frozenset(
    {
        "actual",
        "actual_result",
        "away_score",
        "backtest_profit",
        "barrel",
        "bb_type",
        "bet_result",
        "closing_line",
        "closing_odds",
        "closing_price",
        "edge",
        "eligible_for_backtest",
        "eligible_for_betting",
        "eligible_for_training",
        "elite",
        "estimated_ba_using_speedangle",
        "estimated_woba_using_speedangle",
        "ev",
        "expected_value",
        "fair_probability",
        "final_score",
        "future_outcome",
        "game_status",
        "grade",
        "hit_distance_sc",
        "home_score",
        "inning",
        "inning_topbot",
        "kelly",
        "kelly_fraction",
        "launch_angle",
        "launch_speed",
        "model_probability",
        "payout",
        "postgame_result",
        "predicted_probability",
        "profit",
        "roi",
        "settlement",
        "stake",
        "stake_size",
        "unit",
        "unit_size",
        "units",
        "wager",
        "wager_result",
        "woba_value",
    }
)

_LABEL_PREFIXES: Final = ("label_", "outcome_", "target_")
_LABEL_SUFFIXES: Final = ("_label", "_outcome", "_target")
_FORBIDDEN_PREFIXES: Final = (
    "actual_",
    "closing_",
    "final_",
    "future_",
    "post_game_",
    "postgame_",
    "result_",
    "same_day_",
    "same_game_",
    "settled_",
    "settlement_",
)
_FORBIDDEN_SUFFIXES: Final = (
    "_grade",
    "_payout",
    "_profit",
    "_result",
    "_roi",
    "_settlement",
)


@dataclass(frozen=True, slots=True)
class MLBHRFeatureAvailability:
    """Availability lineage for one feature value on one player-game row."""

    feature_name: str
    available_at: datetime | str
    source_latest_game_date: date | str | None = None


@dataclass(frozen=True, slots=True)
class MLBHRFeaturePackRow:
    """Timestamp lineage for one row in a proposed research feature pack."""

    row_id: str
    game_date: date | str
    odds_collected_at: datetime | str | None
    event_start_time: datetime | str
    feature_availability: tuple[MLBHRFeatureAvailability, ...] = field(
        default_factory=tuple
    )
    feature_cutoff_at: datetime | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_availability", tuple(self.feature_availability))


@dataclass(frozen=True, slots=True)
class MLBHRResearchFeaturePack:
    """In-memory feature declaration; validation never writes or executes it."""

    feature_names: tuple[str, ...]
    rows: tuple[MLBHRFeaturePackRow, ...]
    mode: str = "historical_research"
    approval_status: str = "not_approved"
    model_training_enabled: bool = False
    backtesting_enabled: bool = False
    predictions_enabled: bool = False
    eligible_for_betting: bool = False
    ev_enabled: bool = False
    kelly_eligible: bool = False
    elite_enabled: bool = False
    staking_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_names", tuple(self.feature_names))
        object.__setattr__(self, "rows", tuple(self.rows))


@dataclass(frozen=True, slots=True)
class MLBHRFeatureFirewallResult:
    """Deterministic diagnostics from an allowlist or lineage validation."""

    is_valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    classifications: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "errors", tuple(self.errors))
        object.__setattr__(self, "classifications", tuple(self.classifications))

    def raise_for_errors(self) -> None:
        if not self.is_valid:
            raise MLBHRFeatureFirewallError("; ".join(self.errors))


def _pattern_name(field_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", field_name.casefold()).strip("_")


def classify_mlb_hr_research_field(
    field_name: str,
) -> MLBHRFeatureFieldClass | None:
    """Classify one exact field name; ``None`` means unknown/default-deny."""

    if not isinstance(field_name, str) or not field_name:
        return None
    if field_name in ALLOWED_PREGAME_FEATURES:
        return MLBHRFeatureFieldClass.ALLOWED_PREGAME
    if field_name in ALLOWED_HISTORICAL_ROLLING_FEATURES:
        return MLBHRFeatureFieldClass.ALLOWED_HISTORICAL_ROLLING
    if field_name in ALLOWED_MARKET_FEATURES:
        return MLBHRFeatureFieldClass.ALLOWED_MARKET
    if field_name in LABEL_OUTCOME_FIELDS:
        return MLBHRFeatureFieldClass.LABEL_OUTCOME
    if field_name in FORBIDDEN_LEAKAGE_FIELDS:
        return MLBHRFeatureFieldClass.FORBIDDEN_LEAKAGE

    pattern_name = _pattern_name(field_name)
    if pattern_name.startswith(_FORBIDDEN_PREFIXES) or pattern_name.endswith(
        _FORBIDDEN_SUFFIXES
    ):
        return MLBHRFeatureFieldClass.FORBIDDEN_LEAKAGE
    if pattern_name.startswith(_LABEL_PREFIXES) or pattern_name.endswith(
        _LABEL_SUFFIXES
    ):
        return MLBHRFeatureFieldClass.LABEL_OUTCOME
    return None


def validate_mlb_hr_feature_names(
    feature_names: Iterable[str],
) -> MLBHRFeatureFirewallResult:
    """Reject labels, leakage fields, duplicates, and every unknown feature."""

    if isinstance(feature_names, (str, bytes)):
        return MLBHRFeatureFirewallResult(
            False,
            ("feature_names must be an iterable of exact column names",),
        )
    try:
        names = tuple(feature_names)
    except TypeError:
        return MLBHRFeatureFirewallResult(
            False,
            ("feature_names must be an iterable of exact column names",),
        )

    errors: list[str] = []
    classifications: list[tuple[str, str]] = []
    if not names:
        errors.append("feature set must contain at least one allowlisted column")

    seen: set[str] = set()
    for index, field_name in enumerate(names):
        if not isinstance(field_name, str) or not field_name:
            errors.append(f"feature_names[{index}] must be a non-empty string")
            continue
        if field_name in seen:
            errors.append(f"duplicate feature column: {field_name}")
        seen.add(field_name)

        field_class = classify_mlb_hr_research_field(field_name)
        if field_class is not None:
            classifications.append((field_name, field_class.value))
        if field_class is MLBHRFeatureFieldClass.LABEL_OUTCOME:
            errors.append(f"label/outcome field cannot be used as a feature: {field_name}")
        elif field_class is MLBHRFeatureFieldClass.FORBIDDEN_LEAKAGE:
            errors.append(f"forbidden leakage field cannot be used as a feature: {field_name}")
        elif field_class is None:
            errors.append(
                "unknown feature column is not explicitly allowlisted: " + field_name
            )

    return MLBHRFeatureFirewallResult(
        not errors,
        tuple(dict.fromkeys(errors)),
        tuple(classifications),
    )


def _parse_game_date(value: object, field_name: str, errors: list[str]) -> date | None:
    if isinstance(value, datetime):
        errors.append(f"{field_name} must be a date, not a datetime")
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    errors.append(f"{field_name} must be an ISO-8601 date")
    return None


def _parse_aware_datetime(
    value: object,
    field_name: str,
    errors: list[str],
) -> datetime | None:
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            pass
    if parsed is None:
        errors.append(f"{field_name} must be an ISO-8601 datetime")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(f"{field_name} must be timezone-aware")
        return None
    return parsed


def validate_mlb_hr_feature_pack(
    pack: MLBHRResearchFeaturePack,
) -> MLBHRFeatureFirewallResult:
    """Validate exact feature names and per-row temporal availability lineage."""

    if not isinstance(pack, MLBHRResearchFeaturePack):
        return MLBHRFeatureFirewallResult(
            False,
            ("feature pack has invalid type",),
        )

    name_result = validate_mlb_hr_feature_names(pack.feature_names)
    errors = list(name_result.errors)
    if pack.mode != "historical_research":
        errors.append("feature pack mode must be 'historical_research'")
    if pack.approval_status != "not_approved":
        errors.append("feature pack approval_status must be 'not_approved'")
    enabled_gates = (
        pack.model_training_enabled,
        pack.backtesting_enabled,
        pack.predictions_enabled,
        pack.eligible_for_betting,
        pack.ev_enabled,
        pack.kelly_eligible,
        pack.elite_enabled,
        pack.staking_enabled,
    )
    if any(enabled_gates):
        errors.append("feature pack cannot enable execution, wagering, or approval gates")
    if not pack.rows:
        errors.append("feature pack must contain at least one timestamp-lineage row")

    declared_names = {
        name for name in pack.feature_names if isinstance(name, str) and name
    }
    seen_row_ids: set[str] = set()
    for row_index, row in enumerate(pack.rows):
        row_prefix = f"rows[{row_index}]"
        if not isinstance(row, MLBHRFeaturePackRow):
            errors.append(f"{row_prefix} has invalid type")
            continue
        row_id = row.row_id.strip() if isinstance(row.row_id, str) else ""
        if not row_id:
            errors.append(f"{row_prefix}.row_id is required")
        elif row_id in seen_row_ids:
            errors.append(f"duplicate feature-pack row_id: {row_id}")
        seen_row_ids.add(row_id)

        game_date = _parse_game_date(row.game_date, f"{row_prefix}.game_date", errors)
        odds_time = (
            _parse_aware_datetime(
                row.odds_collected_at,
                f"{row_prefix}.odds_collected_at",
                errors,
            )
            if row.odds_collected_at is not None
            else None
        )
        feature_cutoff = (
            _parse_aware_datetime(
                row.feature_cutoff_at,
                f"{row_prefix}.feature_cutoff_at",
                errors,
            )
            if row.feature_cutoff_at is not None
            else odds_time
        )
        if feature_cutoff is None:
            errors.append(
                f"{row_prefix}.feature_cutoff_at is required when odds are missing"
            )
        if (
            odds_time is not None
            and row.feature_cutoff_at is not None
            and feature_cutoff is not None
            and feature_cutoff != odds_time
        ):
            errors.append(
                f"{row_prefix}.feature_cutoff_at must equal odds_collected_at "
                "when odds are present"
            )
        start_time = _parse_aware_datetime(
            row.event_start_time,
            f"{row_prefix}.event_start_time",
            errors,
        )
        if odds_time is not None and start_time is not None and odds_time >= start_time:
            errors.append(
                f"{row_prefix}.odds_collected_at must be strictly before game start"
            )
        if (
            row.feature_cutoff_at is not None
            and feature_cutoff is not None
            and start_time is not None
            and feature_cutoff >= start_time
        ):
            errors.append(
                f"{row_prefix}.feature_cutoff_at must be strictly before game start"
            )

        lineage_by_name: dict[str, MLBHRFeatureAvailability] = {}
        for lineage_index, lineage in enumerate(row.feature_availability):
            lineage_prefix = f"{row_prefix}.feature_availability[{lineage_index}]"
            if not isinstance(lineage, MLBHRFeatureAvailability):
                errors.append(f"{lineage_prefix} has invalid type")
                continue
            feature_name = lineage.feature_name
            if not isinstance(feature_name, str) or not feature_name:
                errors.append(f"{lineage_prefix}.feature_name must be a non-empty string")
                continue
            if feature_name in lineage_by_name:
                errors.append(
                    f"{row_prefix} has duplicate availability lineage for {feature_name}"
                )
            lineage_by_name[feature_name] = lineage
            if feature_name not in declared_names:
                errors.append(
                    f"{lineage_prefix} references undeclared feature: {feature_name}"
                )

            available_at = _parse_aware_datetime(
                lineage.available_at,
                f"{lineage_prefix}.available_at",
                errors,
            )
            if (
                available_at is not None
                and feature_cutoff is not None
                and available_at > feature_cutoff
            ):
                cutoff_label = (
                    "odds snapshot" if odds_time is not None else "feature cutoff"
                )
                errors.append(
                    f"{row_prefix}.{feature_name} is timestamped after the "
                    f"{cutoff_label}"
                )
            if (
                available_at is not None
                and start_time is not None
                and available_at >= start_time
            ):
                errors.append(
                    f"{row_prefix}.{feature_name} is timestamped at or after game start"
                )

            source_date: date | None = None
            if lineage.source_latest_game_date is not None:
                source_date = _parse_game_date(
                    lineage.source_latest_game_date,
                    f"{lineage_prefix}.source_latest_game_date",
                    errors,
                )
            if feature_name in ALLOWED_HISTORICAL_ROLLING_FEATURES:
                if lineage.source_latest_game_date is None:
                    errors.append(
                        f"{row_prefix}.{feature_name} requires source_latest_game_date"
                    )
                elif (
                    source_date is not None
                    and game_date is not None
                    and source_date >= game_date
                ):
                    errors.append(
                        f"{row_prefix}.{feature_name} includes same-day or future outcomes"
                    )
            elif (
                source_date is not None
                and game_date is not None
                and source_date > game_date
            ):
                errors.append(
                    f"{row_prefix}.{feature_name} has a future effective/source date"
                )

        missing_lineage = sorted(declared_names - set(lineage_by_name))
        if missing_lineage:
            errors.append(
                f"{row_prefix} is missing feature availability lineage: "
                + ", ".join(missing_lineage)
            )

    return MLBHRFeatureFirewallResult(
        not errors,
        tuple(dict.fromkeys(errors)),
        name_result.classifications,
    )


__all__ = [
    "ALLOWED_HISTORICAL_ROLLING_FEATURES",
    "ALLOWED_MARKET_FEATURES",
    "ALLOWED_MLB_HR_RESEARCH_FEATURES",
    "ALLOWED_PREGAME_FEATURES",
    "FORBIDDEN_LEAKAGE_FIELDS",
    "LABEL_OUTCOME_FIELDS",
    "MLBHRFeatureAvailability",
    "MLBHRFeatureFieldClass",
    "MLBHRFeatureFirewallError",
    "MLBHRFeatureFirewallResult",
    "MLBHRFeaturePackRow",
    "MLBHRResearchFeaturePack",
    "classify_mlb_hr_research_field",
    "validate_mlb_hr_feature_names",
    "validate_mlb_hr_feature_pack",
]
