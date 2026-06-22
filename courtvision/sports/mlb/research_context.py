"""Typed, research-only MLB context contracts and deterministic sample fixtures.

This module is a data boundary.  It does not fetch data, score candidates,
approve production use, or participate in bankroll-facing behavior.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime, time, timezone
import math
from types import MappingProxyType
from typing import Final, Literal


MLB_CONTEXT_MODE: Final = "research"
LINEUP_STATUSES: Final = frozenset(
    {"confirmed", "projected", "unknown", "not_starting"}
)
PROBABLE_PITCHER_STATUSES: Final = frozenset(
    {"confirmed", "probable", "projected", "unknown"}
)

LineupStatus = Literal["confirmed", "projected", "unknown", "not_starting"]
ProbablePitcherStatus = Literal[
    "confirmed", "probable", "projected", "unknown"
]


class MLBContextValidationError(ValueError):
    """Raised when an MLB research context fails contract validation."""


@dataclass(frozen=True, slots=True)
class MLBContextValidationResult:
    """Deterministic, non-mutating validation result."""

    is_valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)

    def raise_for_errors(self) -> None:
        if not self.is_valid:
            raise MLBContextValidationError("; ".join(self.errors))


def _tuple_of_text(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be a sequence of strings")
    normalized = tuple(values)
    if any(not isinstance(value, str) for value in normalized):
        raise TypeError(f"{field_name} must contain only strings")
    return normalized


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value.strip()


def _optional_text(value: str | None, field_name: str) -> str | None:
    return None if value is None else _text(value, field_name)


def _source_type(value: str) -> str:
    return _text(value, "source_type").casefold()


def _float_mapping(
    values: Mapping[str, float], field_name: str
) -> Mapping[str, float]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    normalized: dict[str, float] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key.strip():
            raise TypeError(f"{field_name} keys must be non-empty strings")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field_name} values must be numeric")
        normalized[key.strip()] = float(value)
    return MappingProxyType(normalized)


def _serialize(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serialize(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _serialize(getattr(value, item.name))
            for item in fields(value)
        }
    return value


class _SerializableContext:
    def to_dict(self) -> dict[str, object]:
        """Return a stable, JSON-compatible representation."""

        return {
            item.name: _serialize(getattr(self, item.name))
            for item in fields(self)
        }


@dataclass(frozen=True, slots=True)
class MLBTeamContext(_SerializableContext):
    """One team's identity within an MLB game."""

    game_id: str
    team: str
    opponent: str
    is_home: bool
    source_type: str
    collected_at: datetime
    data_quality: str
    warnings: tuple[str, ...] = field(default_factory=tuple)
    sport: str = field(default="MLB", init=False)
    league: str = field(default="MLB", init=False)
    mode: str = field(default=MLB_CONTEXT_MODE, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_type", _source_type(self.source_type))
        object.__setattr__(
            self, "warnings", _tuple_of_text(self.warnings, "warnings")
        )


@dataclass(frozen=True, slots=True)
class MLBGameContext(_SerializableContext):
    """Schedule and venue identity for one MLB game."""

    game_id: str
    game_date: date
    event_start_time: datetime
    home_team: str
    away_team: str
    venue_name: str
    source_type: str
    collected_at: datetime
    data_quality: str
    warnings: tuple[str, ...] = field(default_factory=tuple)
    sport: str = field(default="MLB", init=False)
    league: str = field(default="MLB", init=False)
    mode: str = field(default=MLB_CONTEXT_MODE, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_type", _source_type(self.source_type))
        object.__setattr__(
            self, "warnings", _tuple_of_text(self.warnings, "warnings")
        )


@dataclass(frozen=True, slots=True)
class MLBPlayerLineupStatus(_SerializableContext):
    """One player's explicit place and status in a team lineup."""

    player_id: str
    player_name: str
    bats: str
    batting_order: int | None
    status: LineupStatus | str = "unknown"
    position: str | None = None
    mode: str = field(default=MLB_CONTEXT_MODE, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _text(self.status, "status").casefold())
        object.__setattr__(self, "position", _optional_text(self.position, "position"))

    @property
    def is_confirmed(self) -> bool:
        return self.status == "confirmed"


@dataclass(frozen=True, slots=True)
class MLBLineupContext(_SerializableContext):
    """A team's batting order and confirmation state for one game."""

    game_id: str
    team: str
    lineup_confirmed: bool
    batting_order: tuple[MLBPlayerLineupStatus, ...]
    collected_at: datetime
    source_type: str
    data_quality: str = "unknown"
    warnings: tuple[str, ...] = field(default_factory=tuple)
    sport: str = field(default="MLB", init=False)
    league: str = field(default="MLB", init=False)
    mode: str = field(default=MLB_CONTEXT_MODE, init=False)

    def __post_init__(self) -> None:
        order = tuple(self.batting_order)
        if any(not isinstance(player, MLBPlayerLineupStatus) for player in order):
            raise TypeError(
                "batting_order must contain MLBPlayerLineupStatus values"
            )
        object.__setattr__(self, "batting_order", order)
        object.__setattr__(self, "source_type", _source_type(self.source_type))
        object.__setattr__(
            self, "warnings", _tuple_of_text(self.warnings, "warnings")
        )

    def is_player_confirmed(self, player_id: str) -> bool:
        """Return true only for an explicitly confirmed player."""

        return self.lineup_confirmed and any(
            player.player_id == player_id and player.is_confirmed
            for player in self.batting_order
        )


@dataclass(frozen=True, slots=True)
class MLBProbablePitcherContext(_SerializableContext):
    """Explicit probable-pitcher identity and status for one team."""

    game_id: str
    team: str
    pitcher_id: str
    pitcher_name: str
    throws: str
    probable_status: ProbablePitcherStatus | str
    collected_at: datetime
    source_type: str
    data_quality: str = "unknown"
    warnings: tuple[str, ...] = field(default_factory=tuple)
    sport: str = field(default="MLB", init=False)
    league: str = field(default="MLB", init=False)
    mode: str = field(default=MLB_CONTEXT_MODE, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "probable_status",
            _text(self.probable_status, "probable_status").casefold(),
        )
        object.__setattr__(self, "source_type", _source_type(self.source_type))
        object.__setattr__(
            self, "warnings", _tuple_of_text(self.warnings, "warnings")
        )

    @property
    def is_confirmed(self) -> bool:
        return self.probable_status == "confirmed"


@dataclass(frozen=True, slots=True)
class MLBHitterFeatureContext(_SerializableContext):
    """Provider-neutral hitter features for one explicit sample window."""

    player_id: str
    player_name: str
    bats: str
    sample_window: str
    recent_hr_rate: float | None
    barrel_rate: float | None
    hard_hit_rate: float | None
    fly_ball_rate: float | None
    pull_rate: float | None
    avg_exit_velocity: float | None
    max_exit_velocity: float | None
    source_type: str
    as_of_date: date
    data_quality: str
    mode: str = field(default=MLB_CONTEXT_MODE, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_type", _source_type(self.source_type))


@dataclass(frozen=True, slots=True)
class MLBPitcherFeatureContext(_SerializableContext):
    """Provider-neutral pitcher contact and pitch-mix features."""

    pitcher_id: str
    pitcher_name: str
    throws: str
    pitch_mix: Mapping[str, float]
    hr_allowed_rate: float | None
    barrel_allowed_rate: float | None
    hard_hit_allowed_rate: float | None
    fly_ball_allowed_rate: float | None
    source_type: str
    as_of_date: date
    data_quality: str
    mode: str = field(default=MLB_CONTEXT_MODE, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "pitch_mix", _float_mapping(self.pitch_mix, "pitch_mix"))
        object.__setattr__(self, "source_type", _source_type(self.source_type))


@dataclass(frozen=True, slots=True)
class MLBWeatherContext(_SerializableContext):
    """Observed or supplied weather context for one game."""

    game_id: str
    venue_name: str
    temperature: float | None
    wind_speed: float | None
    wind_direction: str | None
    source_type: str
    collected_at: datetime
    data_quality: str
    wind_out_to_field: str | None = None
    humidity: float | None = None
    roof_status: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    sport: str = field(default="MLB", init=False)
    league: str = field(default="MLB", init=False)
    mode: str = field(default=MLB_CONTEXT_MODE, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_type", _source_type(self.source_type))
        object.__setattr__(
            self, "wind_direction", _optional_text(self.wind_direction, "wind_direction")
        )
        object.__setattr__(
            self,
            "wind_out_to_field",
            _optional_text(self.wind_out_to_field, "wind_out_to_field"),
        )
        object.__setattr__(
            self, "roof_status", _optional_text(self.roof_status, "roof_status")
        )
        object.__setattr__(
            self, "warnings", _tuple_of_text(self.warnings, "warnings")
        )


@dataclass(frozen=True, slots=True)
class MLBBallparkContext(_SerializableContext):
    """Versioned HR-specific ballpark context."""

    venue_name: str
    park_factor_hr: float | None
    source_type: str
    data_version: str
    data_quality: str
    handedness_factor: Mapping[str, float] | None = None
    altitude: float | None = None
    dimensions: Mapping[str, float] | None = None
    roof_type: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    sport: str = field(default="MLB", init=False)
    league: str = field(default="MLB", init=False)
    mode: str = field(default=MLB_CONTEXT_MODE, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_type", _source_type(self.source_type))
        if self.handedness_factor is not None:
            object.__setattr__(
                self,
                "handedness_factor",
                _float_mapping(self.handedness_factor, "handedness_factor"),
            )
        if self.dimensions is not None:
            object.__setattr__(
                self, "dimensions", _float_mapping(self.dimensions, "dimensions")
            )
        object.__setattr__(
            self, "roof_type", _optional_text(self.roof_type, "roof_type")
        )
        object.__setattr__(
            self, "warnings", _tuple_of_text(self.warnings, "warnings")
        )


@dataclass(frozen=True, slots=True)
class MLBHRResearchContext(_SerializableContext):
    """Combined per-hitter context for MLB HR research enrichment."""

    game: MLBGameContext | None
    lineup_status: MLBLineupContext | None
    probable_pitcher: MLBProbablePitcherContext | None
    hitter_features: MLBHitterFeatureContext | None
    pitcher_features: MLBPitcherFeatureContext | None
    weather: MLBWeatherContext | None
    ballpark: MLBBallparkContext | None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    context_complete: bool = field(default=False, init=False)
    missing_required_fields: tuple[str, ...] = field(default_factory=tuple, init=False)
    sport: str = field(default="MLB", init=False)
    league: str = field(default="MLB", init=False)
    mode: str = field(default=MLB_CONTEXT_MODE, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "warnings", _tuple_of_text(self.warnings, "warnings")
        )
        missing = _missing_hr_context_fields(self)
        object.__setattr__(self, "missing_required_fields", missing)
        object.__setattr__(self, "context_complete", not missing)


def _missing_text(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


def _valid_date(value: object) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _valid_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _game_errors(context: MLBGameContext) -> tuple[str, ...]:
    errors: list[str] = []
    for name in (
        "game_id",
        "home_team",
        "away_team",
        "venue_name",
        "source_type",
        "data_quality",
    ):
        if _missing_text(getattr(context, name)):
            errors.append(f"game.{name} is required")
    if not _valid_date(context.game_date):
        errors.append("game.game_date must be a date")
    if not isinstance(context.event_start_time, datetime):
        errors.append("game.event_start_time must be a datetime")
    if not isinstance(context.collected_at, datetime):
        errors.append("game.collected_at must be a datetime")
    if context.sport != "MLB" or context.league != "MLB":
        errors.append("game sport and league must be MLB")
    if context.mode != MLB_CONTEXT_MODE:
        errors.append("game.mode must remain research")
    return tuple(errors)


def validate_game_context(context: MLBGameContext) -> MLBContextValidationResult:
    if not isinstance(context, MLBGameContext):
        return MLBContextValidationResult(False, ("game has invalid type",))
    errors = _game_errors(context)
    return MLBContextValidationResult(not errors, errors)


def _lineup_errors(context: MLBLineupContext) -> tuple[str, ...]:
    errors: list[str] = []
    for name in ("game_id", "team", "source_type", "data_quality"):
        if _missing_text(getattr(context, name)):
            errors.append(f"lineup_status.{name} is required")
    if not isinstance(context.lineup_confirmed, bool):
        errors.append("lineup_status.lineup_confirmed must be a bool")
    if not isinstance(context.collected_at, datetime):
        errors.append("lineup_status.collected_at must be a datetime")
    if not context.batting_order:
        errors.append("lineup_status.batting_order is required")
    seen_orders: set[int] = set()
    for index, player in enumerate(context.batting_order):
        prefix = f"lineup_status.batting_order[{index}]"
        for name in ("player_id", "player_name", "bats"):
            if _missing_text(getattr(player, name)):
                errors.append(f"{prefix}.{name} is required")
        if player.status not in LINEUP_STATUSES:
            errors.append(f"{prefix}.status is unsupported")
        if player.batting_order is not None:
            if (
                isinstance(player.batting_order, bool)
                or not isinstance(player.batting_order, int)
                or not 1 <= player.batting_order <= 9
            ):
                errors.append(f"{prefix}.batting_order must be from 1 to 9")
            elif player.batting_order in seen_orders:
                errors.append(f"{prefix}.batting_order is duplicated")
            else:
                seen_orders.add(player.batting_order)
    if context.lineup_confirmed and any(
        player.status in {"projected", "unknown"}
        for player in context.batting_order
    ):
        errors.append(
            "lineup_status cannot be confirmed while a listed player is projected or unknown"
        )
    if context.mode != MLB_CONTEXT_MODE:
        errors.append("lineup_status.mode must remain research")
    return tuple(errors)


def validate_lineup_context(context: MLBLineupContext) -> MLBContextValidationResult:
    if not isinstance(context, MLBLineupContext):
        return MLBContextValidationResult(False, ("lineup_status has invalid type",))
    errors = _lineup_errors(context)
    return MLBContextValidationResult(not errors, errors)


def _pitcher_errors(context: MLBProbablePitcherContext) -> tuple[str, ...]:
    errors: list[str] = []
    for name in (
        "game_id",
        "team",
        "pitcher_id",
        "pitcher_name",
        "throws",
        "source_type",
        "data_quality",
    ):
        if _missing_text(getattr(context, name)):
            errors.append(f"probable_pitcher.{name} is required")
    if context.probable_status not in PROBABLE_PITCHER_STATUSES:
        errors.append("probable_pitcher.probable_status is unsupported")
    if not isinstance(context.collected_at, datetime):
        errors.append("probable_pitcher.collected_at must be a datetime")
    if context.mode != MLB_CONTEXT_MODE:
        errors.append("probable_pitcher.mode must remain research")
    return tuple(errors)


def validate_probable_pitcher_context(
    context: MLBProbablePitcherContext,
) -> MLBContextValidationResult:
    if not isinstance(context, MLBProbablePitcherContext):
        return MLBContextValidationResult(False, ("probable_pitcher has invalid type",))
    errors = _pitcher_errors(context)
    return MLBContextValidationResult(not errors, errors)


def _missing_feature_fields(prefix: str, value: object, names: Sequence[str]) -> list[str]:
    missing: list[str] = []
    for name in names:
        current = getattr(value, name)
        if current is None or (_missing_text(current) if isinstance(current, str) else False):
            missing.append(f"{prefix}.{name}")
    return missing


def _missing_hr_context_fields(context: MLBHRResearchContext) -> tuple[str, ...]:
    missing: list[str] = []
    components = (
        "game",
        "lineup_status",
        "probable_pitcher",
        "hitter_features",
        "pitcher_features",
        "weather",
        "ballpark",
    )
    for name in components:
        if getattr(context, name) is None:
            missing.append(name)

    if context.game is not None:
        missing.extend(error.removesuffix(" is required") for error in _game_errors(context.game))
    if context.lineup_status is not None:
        missing.extend(
            error.removesuffix(" is required")
            for error in _lineup_errors(context.lineup_status)
        )
    if context.probable_pitcher is not None:
        missing.extend(
            error.removesuffix(" is required")
            for error in _pitcher_errors(context.probable_pitcher)
        )

    hitter = context.hitter_features
    if hitter is not None:
        missing.extend(
            _missing_feature_fields(
                "hitter_features",
                hitter,
                (
                    "player_id",
                    "player_name",
                    "bats",
                    "sample_window",
                    "recent_hr_rate",
                    "barrel_rate",
                    "hard_hit_rate",
                    "fly_ball_rate",
                    "pull_rate",
                    "avg_exit_velocity",
                    "max_exit_velocity",
                    "source_type",
                    "data_quality",
                ),
            )
        )
        if not _valid_date(hitter.as_of_date):
            missing.append("hitter_features.as_of_date")

    pitcher = context.pitcher_features
    if pitcher is not None:
        missing.extend(
            _missing_feature_fields(
                "pitcher_features",
                pitcher,
                (
                    "pitcher_id",
                    "pitcher_name",
                    "throws",
                    "hr_allowed_rate",
                    "barrel_allowed_rate",
                    "hard_hit_allowed_rate",
                    "fly_ball_allowed_rate",
                    "source_type",
                    "data_quality",
                ),
            )
        )
        if not pitcher.pitch_mix:
            missing.append("pitcher_features.pitch_mix")
        if not _valid_date(pitcher.as_of_date):
            missing.append("pitcher_features.as_of_date")

    weather = context.weather
    if weather is not None:
        missing.extend(
            _missing_feature_fields(
                "weather",
                weather,
                (
                    "game_id",
                    "venue_name",
                    "temperature",
                    "wind_speed",
                    "wind_direction",
                    "source_type",
                    "data_quality",
                ),
            )
        )
        if not isinstance(weather.collected_at, datetime):
            missing.append("weather.collected_at")

    ballpark = context.ballpark
    if ballpark is not None:
        missing.extend(
            _missing_feature_fields(
                "ballpark",
                ballpark,
                (
                    "venue_name",
                    "park_factor_hr",
                    "source_type",
                    "data_version",
                    "data_quality",
                ),
            )
        )

    if context.lineup_status is not None and hitter is not None:
        player = next(
            (
                item
                for item in context.lineup_status.batting_order
                if item.player_id == hitter.player_id
            ),
            None,
        )
        if player is None:
            missing.append("lineup_status.hitter_player_id")
        elif player.status in {"unknown", "not_starting"}:
            missing.append("lineup_status.hitter_status")

    if context.probable_pitcher is not None:
        if context.probable_pitcher.probable_status == "unknown":
            missing.append("probable_pitcher.probable_status")
        if pitcher is not None and (
            context.probable_pitcher.pitcher_id != pitcher.pitcher_id
        ):
            missing.append("pitcher_features.pitcher_id_match")

    game = context.game
    if game is not None:
        for component, field_name in (
            (context.lineup_status, "game_id"),
            (context.probable_pitcher, "game_id"),
            (weather, "game_id"),
        ):
            if component is not None and getattr(component, field_name) != game.game_id:
                missing.append(f"{type(component).__name__}.game_id_match")
        if weather is not None and weather.venue_name != game.venue_name:
            missing.append("weather.venue_name_match")
        if ballpark is not None and ballpark.venue_name != game.venue_name:
            missing.append("ballpark.venue_name_match")

    return tuple(dict.fromkeys(missing))


def validate_hr_research_context(
    context: MLBHRResearchContext,
) -> MLBContextValidationResult:
    """Validate all required research pieces and cross-object identities."""

    if not isinstance(context, MLBHRResearchContext):
        return MLBContextValidationResult(False, ("context has invalid type",))
    errors = tuple(f"{name} is missing or invalid" for name in context.missing_required_fields)
    if context.mode != MLB_CONTEXT_MODE:
        errors += ("context.mode must remain research",)
    if context.context_complete != (not context.missing_required_fields):
        errors += ("context completeness fields are inconsistent",)
    return MLBContextValidationResult(not errors, errors)


def summarize_context_warnings(context: MLBHRResearchContext) -> tuple[str, ...]:
    """Collect explicit warnings without hiding missing context."""

    if not isinstance(context, MLBHRResearchContext):
        raise TypeError("context must be an MLBHRResearchContext")
    warnings: list[str] = list(context.warnings)
    for component in (
        context.game,
        context.lineup_status,
        context.probable_pitcher,
        context.weather,
        context.ballpark,
    ):
        if component is not None:
            warnings.extend(component.warnings)
    warnings.extend(
        f"Missing or invalid required context: {name}."
        for name in context.missing_required_fields
    )
    return tuple(dict.fromkeys(warnings))


def context_is_complete_for_research(context: MLBHRResearchContext) -> bool:
    """Return true only when every required research contract piece is valid."""

    return validate_hr_research_context(context).is_valid


def context_is_complete_for_production(context: MLBHRResearchContext) -> bool:
    """MLB has no production-approved context path in Phase 2B."""

    return False


_SAMPLE_PLAYER_METADATA: Final = (
    ("example-player", "R", 3, "RF", "example-pitcher", "R", 0.105, 0.410, 0.360),
    ("sample-slugger", "L", 2, "1B", "sample-starter", "R", 0.092, 0.385, 0.342),
    ("demo-batter", "L", 5, "DH", "demo-pitcher", "L", 0.073, 0.351, 0.318),
)


def build_sample_mlb_hr_contexts(
    report_date: date,
) -> tuple[MLBHRResearchContext, ...]:
    """Build deterministic, keyless contexts aligned to the sample HR slate."""

    if isinstance(report_date, datetime) or not isinstance(report_date, date):
        raise TypeError("report_date must be a date")

    # Local import keeps the contracts independent from provider construction.
    from courtvision.sports.mlb.adapters.sample_provider import sample_hr_props

    candidates = sample_hr_props(report_date)
    collected_at = datetime.combine(report_date, time(12, 0), tzinfo=timezone.utc)
    contexts: list[MLBHRResearchContext] = []
    for index, (candidate, metadata) in enumerate(
        zip(candidates, _SAMPLE_PLAYER_METADATA, strict=True), start=1
    ):
        (
            player_id,
            bats,
            batting_order,
            position,
            pitcher_id,
            throws,
            barrel_allowed_rate,
            hard_hit_allowed_rate,
            fly_ball_allowed_rate,
        ) = metadata
        game_id = f"mlb-sample-{report_date.isoformat()}-{index:03d}"
        event_start = (
            candidate.game_time
            if isinstance(candidate.game_time, datetime)
            else datetime.combine(report_date, time(19, 0))
        )
        common_warning = ("Sample source; research context only.",)
        game = MLBGameContext(
            game_id=game_id,
            game_date=report_date,
            event_start_time=event_start,
            home_team=candidate.team,
            away_team=candidate.opponent,
            venue_name=candidate.venue,
            source_type="sample",
            collected_at=collected_at,
            data_quality="sample_data",
            warnings=common_warning,
        )
        lineup = MLBLineupContext(
            game_id=game_id,
            team=candidate.team,
            lineup_confirmed=True,
            batting_order=(
                MLBPlayerLineupStatus(
                    player_id=player_id,
                    player_name=candidate.player,
                    bats=bats,
                    batting_order=batting_order,
                    position=position,
                    status="confirmed",
                ),
            ),
            collected_at=collected_at,
            source_type="sample",
            data_quality="sample_data",
            warnings=common_warning,
        )
        probable_pitcher = MLBProbablePitcherContext(
            game_id=game_id,
            team=candidate.opponent,
            pitcher_id=pitcher_id,
            pitcher_name=candidate.pitcher,
            throws=throws,
            probable_status="confirmed",
            collected_at=collected_at,
            source_type="sample",
            data_quality="sample_data",
            warnings=common_warning,
        )
        hitter = MLBHitterFeatureContext(
            player_id=player_id,
            player_name=candidate.player,
            bats=bats,
            sample_window=f"recent_{candidate.recent_plate_appearances}_pa",
            recent_hr_rate=(
                candidate.recent_home_runs / candidate.recent_plate_appearances
            ),
            barrel_rate=candidate.barrel_rate,
            hard_hit_rate=candidate.hard_hit_rate,
            fly_ball_rate=candidate.fly_ball_rate,
            pull_rate=candidate.pull_rate,
            avg_exit_velocity=candidate.average_exit_velocity,
            max_exit_velocity=candidate.max_exit_velocity,
            source_type="sample",
            as_of_date=report_date,
            data_quality="sample_data",
        )
        pitcher = MLBPitcherFeatureContext(
            pitcher_id=pitcher_id,
            pitcher_name=candidate.pitcher,
            throws=throws,
            pitch_mix=candidate.pitcher_pitch_mix,
            hr_allowed_rate=candidate.pitcher_hr_allowed_rate,
            barrel_allowed_rate=barrel_allowed_rate,
            hard_hit_allowed_rate=hard_hit_allowed_rate,
            fly_ball_allowed_rate=fly_ball_allowed_rate,
            source_type="sample",
            as_of_date=report_date,
            data_quality="sample_data",
        )
        weather = MLBWeatherContext(
            game_id=game_id,
            venue_name=candidate.venue,
            temperature=candidate.temperature,
            wind_speed=candidate.wind_speed,
            wind_direction=candidate.wind_direction,
            wind_out_to_field=(
                candidate.wind_direction.removeprefix("blowing out to ")
                if "blowing out to " in candidate.wind_direction.casefold()
                else None
            ),
            humidity=55.0,
            roof_status="unknown",
            source_type="sample",
            collected_at=collected_at,
            data_quality="sample_data",
            warnings=common_warning,
        )
        ballpark = MLBBallparkContext(
            venue_name=candidate.venue,
            park_factor_hr=candidate.ballpark_hr_factor,
            handedness_factor={"L": candidate.ballpark_hr_factor, "R": candidate.ballpark_hr_factor},
            altitude=None,
            dimensions=None,
            source_type="sample",
            data_version=f"sample-{report_date.isoformat()}",
            data_quality="sample_data",
            warnings=common_warning,
        )
        contexts.append(
            MLBHRResearchContext(
                game=game,
                lineup_status=lineup,
                probable_pitcher=probable_pitcher,
                hitter_features=hitter,
                pitcher_features=pitcher,
                weather=weather,
                ballpark=ballpark,
                warnings=(
                    "Deterministic fixture; not externally collected.",
                    "Research completeness does not imply production approval.",
                ),
            )
        )
    return tuple(contexts)


__all__ = [
    "LINEUP_STATUSES",
    "MLB_CONTEXT_MODE",
    "PROBABLE_PITCHER_STATUSES",
    "LineupStatus",
    "MLBBallparkContext",
    "MLBContextValidationError",
    "MLBContextValidationResult",
    "MLBGameContext",
    "MLBHitterFeatureContext",
    "MLBHRResearchContext",
    "MLBLineupContext",
    "MLBPitcherFeatureContext",
    "MLBPlayerLineupStatus",
    "MLBProbablePitcherContext",
    "MLBTeamContext",
    "MLBWeatherContext",
    "ProbablePitcherStatus",
    "build_sample_mlb_hr_contexts",
    "context_is_complete_for_production",
    "context_is_complete_for_research",
    "summarize_context_warnings",
    "validate_game_context",
    "validate_hr_research_context",
    "validate_lineup_context",
    "validate_probable_pitcher_context",
]
