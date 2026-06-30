"""Read-only statistical-power gates for MLB HR temporal windows.

The validator binds one feature pack, one strict temporal split plan, and one
sealed preprocessing artifact before measuring any window.  It does not train,
transform, predict, backtest, fetch, write artifacts, or enable wagering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
import json
from pathlib import Path
from types import MappingProxyType
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.training.hr_preprocessing_artifact import (
    MLBHRFittedPreprocessingArtifactError,
    load_fitted_preprocessing_artifact,
)
from courtvision.sports.mlb.training.hr_preprocessing_plan import (
    MLBHRPreprocessingPlanningError,
    plan_mlb_hr_preprocessing,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    MLBHRLabelCustodyError,
    MLBHRLabelOpeningAuthorization,
    open_mlb_hr_label_custody_split,
    resolve_label_custody_path,
)


class MLBHRWindowReadinessError(ValueError):
    """Raised when prerequisite artifacts or window evidence are invalid."""


class MLBHRWindowReadinessVerdict(StrEnum):
    """Default-deny verdict for one temporal research window."""

    WINDOW_NOT_READY = "WINDOW_NOT_READY"
    WINDOW_READY_FOR_REVIEW = "WINDOW_READY_FOR_REVIEW"
    WINDOW_READY_FOR_RESEARCH_BACKTEST = "WINDOW_READY_FOR_RESEARCH_BACKTEST"


@dataclass(frozen=True, slots=True)
class MLBHRWindowThresholds:
    """Minimum independent player-game evidence for one window."""

    player_game_rows: int
    unique_games: int
    unique_players: int
    positive_labels: int
    negative_labels: int
    odds_coverage: float
    weather_coverage: float
    ballpark_coverage: float
    date_span_days: int


# The review floor only establishes that both classes and every context source
# are represented.  It is deliberately not sufficient for a backtest.
WINDOW_REVIEW_THRESHOLDS: Final = MLBHRWindowThresholds(
    player_game_rows=20,
    unique_games=5,
    unique_players=10,
    positive_labels=1,
    negative_labels=10,
    odds_coverage=0.50,
    weather_coverage=0.80,
    ballpark_coverage=0.80,
    date_span_days=3,
)

# Evaluation windows require 1,000 independent player-games and 50 positive
# labels.  At a 5% HR rate this supports an approximate 95% event-rate margin
# of error of 1.35 percentage points.  The larger train floor preserves at
# least 100 positives for fitting work that may be approved later.
WINDOW_RESEARCH_THRESHOLDS: Final[Mapping[str, MLBHRWindowThresholds]] = (
    MappingProxyType(
        {
            "train": MLBHRWindowThresholds(
                player_game_rows=2_000,
                unique_games=200,
                unique_players=200,
                positive_labels=100,
                negative_labels=1_000,
                odds_coverage=0.80,
                weather_coverage=0.95,
                ballpark_coverage=0.95,
                date_span_days=90,
            ),
            "validation": MLBHRWindowThresholds(
                player_game_rows=1_000,
                unique_games=100,
                unique_players=100,
                positive_labels=50,
                negative_labels=500,
                odds_coverage=0.80,
                weather_coverage=0.95,
                ballpark_coverage=0.95,
                date_span_days=30,
            ),
            "test": MLBHRWindowThresholds(
                player_game_rows=1_000,
                unique_games=100,
                unique_players=100,
                positive_labels=50,
                negative_labels=500,
                odds_coverage=0.80,
                weather_coverage=0.95,
                ballpark_coverage=0.95,
                date_span_days=30,
            ),
        }
    )
)

_WEATHER_FIELDS: Final = (
    "weather_temperature",
    "weather_wind_speed",
    "weather_wind_direction",
    "weather_wind_out_to_field",
    "weather_humidity",
    "roof_status",
)
_BALLPARK_FIELDS: Final = (
    "park_factor_hr",
    "park_factor_lhb",
    "park_factor_rhb",
    "altitude",
)
_ODDS_FIELDS: Final = (
    "sportsbook",
    "odds_provider",
    "american_odds",
    "decimal_odds",
    "implied_probability",
    "odds_collected_at",
    "odds_as_of",
)


@dataclass(frozen=True, slots=True)
class MLBHRWindowMetrics:
    """Observed independent player-game metrics for one temporal window."""

    name: str
    player_game_rows: int
    unique_games: int
    unique_players: int
    positive_labels: int
    negative_labels: int
    market_covered_rows: int
    market_missing_rows: int
    odds_covered_rows: int
    odds_coverage: float
    weather_covered_rows: int
    weather_coverage: float
    ballpark_covered_rows: int
    ballpark_coverage: float
    date_start: date | None
    date_end: date | None
    date_span_days: int
    verdict: str
    review_failures: tuple[str, ...] = field(default_factory=tuple)
    research_failures: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "review_failures", tuple(self.review_failures))
        object.__setattr__(self, "research_failures", tuple(self.research_failures))


@dataclass(frozen=True, slots=True)
class MLBHRWindowReadinessReport:
    """Bound, read-only readiness result for all three temporal windows."""

    feature_pack_path: Path
    temporal_split_plan_path: Path
    fitted_preprocessing_artifact_path: Path
    windows: tuple[MLBHRWindowMetrics, ...]
    verdict: str
    feature_firewall_valid: bool = True
    temporal_split_valid: bool = True
    preprocessing_artifact_hash_match: bool = True
    research_only: bool = True
    approval_status: str = "not_approved"
    model_training_enabled: bool = False
    backtesting_enabled: bool = False
    predictions_enabled: bool = False
    live_fetching_enabled: bool = False
    eligible_for_betting: bool = False
    ev_enabled: bool = False
    kelly_eligible: bool = False
    elite_enabled: bool = False
    staking_enabled: bool = False
    artifacts_written: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "feature_pack_path",
            "temporal_split_plan_path",
            "fitted_preprocessing_artifact_path",
        ):
            object.__setattr__(
                self, field_name, getattr(self, field_name).resolve()
            )
        object.__setattr__(self, "windows", tuple(self.windows))
        if self.approval_status != "not_approved" or not self.research_only:
            raise MLBHRWindowReadinessError(
                "window readiness cannot grant production approval"
            )
        if any(
            (
                self.model_training_enabled,
                self.backtesting_enabled,
                self.predictions_enabled,
                self.live_fetching_enabled,
                self.eligible_for_betting,
                self.ev_enabled,
                self.kelly_eligible,
                self.elite_enabled,
                self.staking_enabled,
                self.artifacts_written,
            )
        ):
            raise MLBHRWindowReadinessError(
                "window readiness cannot enable execution, wagering, or writes"
            )

    @property
    def ready_for_research_backtest(self) -> bool:
        return self.verdict == (
            MLBHRWindowReadinessVerdict.WINDOW_READY_FOR_RESEARCH_BACKTEST.value
        )


@dataclass(slots=True)
class _PlayerGameEvidence:
    game_date: date
    game_id: str
    player_id: str
    is_home_run: bool
    market_available: bool
    odds_covered: bool
    weather_covered: bool
    ballpark_covered: bool


def _read_json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MLBHRWindowReadinessError(
            f"could not read {label} {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise MLBHRWindowReadinessError(f"{label} must contain a JSON object")
    return payload


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MLBHRWindowReadinessError(f"{field_name} must be non-empty text")
    return value.strip()


def _required_date(value: object, field_name: str) -> date:
    if not isinstance(value, str):
        raise MLBHRWindowReadinessError(
            f"{field_name} must be an ISO-8601 date"
        )
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise MLBHRWindowReadinessError(
            f"{field_name} must be an ISO-8601 date"
        ) from exc


def _present(value: object) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _complete(values: Mapping[str, object], fields: Sequence[str]) -> bool:
    return all(
        field_name in values and _present(values[field_name])
        for field_name in fields
    )


def _row_coverage(
    values: Mapping[str, object],
) -> tuple[bool, bool, bool, bool]:
    market_available = values.get("hr_market_available")
    if not isinstance(market_available, bool):
        raise MLBHRWindowReadinessError(
            "hr_market_available must be an explicit boolean"
        )
    odds_covered = (
        market_available
        and values.get("odds_is_fresh_for_pregame") is True
        and _complete(values, _ODDS_FIELDS)
    )
    return (
        market_available,
        odds_covered,
        _complete(values, _WEATHER_FIELDS),
        _complete(values, _BALLPARK_FIELDS),
    )


def _feature_rows(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise MLBHRWindowReadinessError(
            "feature-pack rows must be a non-empty list"
        )
    rows: list[Mapping[str, object]] = []
    for index, raw_row in enumerate(raw_rows):
        if not isinstance(raw_row, Mapping):
            raise MLBHRWindowReadinessError(
                f"feature-pack rows[{index}] must be an object"
            )
        rows.append(raw_row)
    return tuple(rows)


def _player_game_evidence(
    rows: Sequence[Mapping[str, object]],
    *,
    date_to_window: Mapping[date, str],
    labels_by_row_id: Mapping[str, bool],
) -> dict[str, tuple[_PlayerGameEvidence, ...]]:
    by_key: dict[tuple[date, str, str], _PlayerGameEvidence] = {}
    for index, row in enumerate(rows):
        prefix = f"feature-pack rows[{index}]"
        game_date = _required_date(row.get("game_date"), f"{prefix}.game_date")
        if game_date not in date_to_window:
            raise MLBHRWindowReadinessError(
                f"{prefix}.game_date is outside the temporal split: {game_date}"
            )
        game_id = _required_text(row.get("game_id"), f"{prefix}.game_id")
        player_id = _required_text(row.get("player_id"), f"{prefix}.player_id")
        row_id = _required_text(row.get("row_id"), f"{prefix}.row_id")
        label = labels_by_row_id.get(row_id)
        if type(label) is not bool:
            raise MLBHRWindowReadinessError(
                f"{prefix} has no authorized explicit boolean label"
            )
        values = row.get("feature_values")
        if not isinstance(values, Mapping):
            raise MLBHRWindowReadinessError(
                f"{prefix}.feature_values must be an object"
            )
        market_available, odds, weather, ballpark = _row_coverage(values)
        segments = row.get("segments")
        if segments is not None:
            if not isinstance(segments, Mapping):
                raise MLBHRWindowReadinessError(
                    f"{prefix}.segments must be an object"
                )
            expected_segment = (
                "market_covered" if market_available else "market_missing"
            )
            if segments.get("market_coverage") != expected_segment:
                raise MLBHRWindowReadinessError(
                    f"{prefix}.segments.market_coverage must be "
                    f"{expected_segment!r}"
                )
        key = (game_date, game_id, player_id)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = _PlayerGameEvidence(
                game_date=game_date,
                game_id=game_id,
                player_id=player_id,
                is_home_run=label,
                market_available=market_available,
                odds_covered=odds,
                weather_covered=weather,
                ballpark_covered=ballpark,
            )
            continue
        if existing.is_home_run is not label:
            raise MLBHRWindowReadinessError(
                "feature pack has conflicting labels for player-game "
                f"{game_date}/{game_id}/{player_id}"
            )
        # Multiple sportsbook rows do not add statistical power.  Coverage is
        # credited when at least one row has complete pregame evidence.
        existing.market_available = existing.market_available or market_available
        existing.odds_covered = existing.odds_covered or odds
        existing.weather_covered = existing.weather_covered or weather
        existing.ballpark_covered = existing.ballpark_covered or ballpark

    grouped: dict[str, list[_PlayerGameEvidence]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    for evidence in by_key.values():
        grouped[date_to_window[evidence.game_date]].append(evidence)
    return {
        name: tuple(
            sorted(
                values,
                key=lambda item: (item.game_date, item.game_id, item.player_id),
            )
        )
        for name, values in grouped.items()
    }


def _threshold_failures(
    *,
    player_game_rows: int,
    unique_games: int,
    unique_players: int,
    positive_labels: int,
    negative_labels: int,
    odds_coverage: float,
    weather_coverage: float,
    ballpark_coverage: float,
    date_span_days: int,
    thresholds: MLBHRWindowThresholds,
) -> tuple[str, ...]:
    actuals = (
        ("player_game_rows", player_game_rows, thresholds.player_game_rows),
        ("unique_games", unique_games, thresholds.unique_games),
        ("unique_players", unique_players, thresholds.unique_players),
        ("positive_labels", positive_labels, thresholds.positive_labels),
        ("negative_labels", negative_labels, thresholds.negative_labels),
        ("date_span_days", date_span_days, thresholds.date_span_days),
    )
    failures = [
        f"{name}={actual} requires >= {minimum}"
        for name, actual, minimum in actuals
        if actual < minimum
    ]
    coverages = (
        ("odds_coverage", odds_coverage, thresholds.odds_coverage),
        ("weather_coverage", weather_coverage, thresholds.weather_coverage),
        ("ballpark_coverage", ballpark_coverage, thresholds.ballpark_coverage),
    )
    failures.extend(
        f"{name}={actual:.2%} requires >= {minimum:.2%}"
        for name, actual, minimum in coverages
        if actual < minimum
    )
    return tuple(failures)


def _window_metrics(
    name: str,
    evidence: Sequence[_PlayerGameEvidence],
) -> MLBHRWindowMetrics:
    row_count = len(evidence)
    unique_games = len({item.game_id for item in evidence})
    unique_players = len({item.player_id for item in evidence})
    positives = sum(item.is_home_run for item in evidence)
    negatives = row_count - positives
    market_rows = sum(item.market_available for item in evidence)
    market_missing_rows = row_count - market_rows
    odds_rows = sum(item.odds_covered for item in evidence)
    weather_rows = sum(item.weather_covered for item in evidence)
    ballpark_rows = sum(item.ballpark_covered for item in evidence)
    odds_rate = odds_rows / row_count if row_count else 0.0
    weather_rate = weather_rows / row_count if row_count else 0.0
    ballpark_rate = ballpark_rows / row_count if row_count else 0.0
    dates = [item.game_date for item in evidence]
    start = min(dates) if dates else None
    end = max(dates) if dates else None
    span = (end - start).days + 1 if start is not None and end is not None else 0
    kwargs = {
        "player_game_rows": row_count,
        "unique_games": unique_games,
        "unique_players": unique_players,
        "positive_labels": positives,
        "negative_labels": negatives,
        "odds_coverage": odds_rate,
        "weather_coverage": weather_rate,
        "ballpark_coverage": ballpark_rate,
        "date_span_days": span,
    }
    review_failures = _threshold_failures(
        **kwargs, thresholds=WINDOW_REVIEW_THRESHOLDS
    )
    research_failures = _threshold_failures(
        **kwargs, thresholds=WINDOW_RESEARCH_THRESHOLDS[name]
    )
    if review_failures:
        verdict = MLBHRWindowReadinessVerdict.WINDOW_NOT_READY.value
    elif research_failures:
        verdict = MLBHRWindowReadinessVerdict.WINDOW_READY_FOR_REVIEW.value
    else:
        verdict = (
            MLBHRWindowReadinessVerdict.WINDOW_READY_FOR_RESEARCH_BACKTEST.value
        )
    return MLBHRWindowMetrics(
        name=name,
        market_covered_rows=market_rows,
        market_missing_rows=market_missing_rows,
        odds_covered_rows=odds_rows,
        weather_covered_rows=weather_rows,
        ballpark_covered_rows=ballpark_rows,
        date_start=start,
        date_end=end,
        verdict=verdict,
        review_failures=review_failures,
        research_failures=research_failures,
        **kwargs,
    )


def _overall_verdict(windows: Sequence[MLBHRWindowMetrics]) -> str:
    verdicts = {window.verdict for window in windows}
    if MLBHRWindowReadinessVerdict.WINDOW_NOT_READY.value in verdicts:
        return MLBHRWindowReadinessVerdict.WINDOW_NOT_READY.value
    if MLBHRWindowReadinessVerdict.WINDOW_READY_FOR_REVIEW.value in verdicts:
        return MLBHRWindowReadinessVerdict.WINDOW_READY_FOR_REVIEW.value
    return MLBHRWindowReadinessVerdict.WINDOW_READY_FOR_RESEARCH_BACKTEST.value


def validate_mlb_hr_window_readiness(
    *,
    feature_pack_path: str | Path,
    label_custody_path: str | Path | None = None,
    temporal_split_plan_path: str | Path,
    fitted_preprocessing_artifact_path: str | Path,
    opening_authorizations: Sequence[MLBHRLabelOpeningAuthorization] = (),
) -> MLBHRWindowReadinessReport:
    """Measure windows only after every split has explicit label authority."""

    feature_source = Path(feature_pack_path).expanduser().resolve()
    custody_source = resolve_label_custody_path(
        feature_source, label_custody_path
    )
    split_source = Path(temporal_split_plan_path).expanduser().resolve()
    preprocessing_source = (
        Path(fitted_preprocessing_artifact_path).expanduser().resolve()
    )
    try:
        plan = plan_mlb_hr_preprocessing(
            feature_pack_path=feature_source,
            temporal_split_plan_path=split_source,
        )
        load_fitted_preprocessing_artifact(
            preprocessing_source,
            feature_pack_path=feature_source,
            temporal_split_plan_path=split_source,
        )
    except (
        MLBHRPreprocessingPlanningError,
        MLBHRFittedPreprocessingArtifactError,
    ) as exc:
        raise MLBHRWindowReadinessError(str(exc)) from exc

    date_to_window: dict[date, str] = {}
    for window in (
        plan.split_plan.train,
        plan.split_plan.validation,
        plan.split_plan.test,
    ):
        for game_date in window.game_dates:
            if game_date in date_to_window:
                raise MLBHRWindowReadinessError(
                    f"game date appears in multiple windows: {game_date}"
                )
            date_to_window[game_date] = window.name

    payload = _read_json_object(feature_source, "feature pack")
    authorizations = {item.split: item for item in opening_authorizations}
    if set(authorizations) != {"train", "validation", "test"}:
        raise MLBHRWindowReadinessError(
            "window label readiness requires explicit train, validation, and "
            "approved test opening authorizations"
        )
    labels_by_row_id: dict[str, bool] = {}
    try:
        for split_name in ("train", "validation", "test"):
            opened = open_mlb_hr_label_custody_split(
                feature_pack_path=feature_source,
                label_custody_path=custody_source,
                temporal_split_plan_path=split_source,
                authorization=authorizations[split_name],
            )
            labels_by_row_id.update(
                {item.row_id: item.is_home_run for item in opened}
            )
    except MLBHRLabelCustodyError as exc:
        raise MLBHRWindowReadinessError(
            f"authorized window label opening failed: {exc}"
        ) from exc
    grouped = _player_game_evidence(
        _feature_rows(payload),
        date_to_window=date_to_window,
        labels_by_row_id=labels_by_row_id,
    )
    windows = tuple(
        _window_metrics(name, grouped[name])
        for name in ("train", "validation", "test")
    )
    return MLBHRWindowReadinessReport(
        feature_pack_path=feature_source,
        temporal_split_plan_path=split_source,
        fitted_preprocessing_artifact_path=preprocessing_source,
        windows=windows,
        verdict=_overall_verdict(windows),
    )


__all__ = [
    "MLBHRWindowMetrics",
    "MLBHRWindowReadinessError",
    "MLBHRWindowReadinessReport",
    "MLBHRWindowReadinessVerdict",
    "MLBHRWindowThresholds",
    "WINDOW_RESEARCH_THRESHOLDS",
    "WINDOW_REVIEW_THRESHOLDS",
    "validate_mlb_hr_window_readiness",
]
