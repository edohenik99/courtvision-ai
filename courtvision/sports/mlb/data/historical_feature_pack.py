"""Research-only feature-pack builder for staged MLB HR historical data.

The builder composes the immutable input-pack preflight, the research-backtest
readiness gate, and the timestamp-aware feature firewall.  It reads only the
six fixed staged CSVs, never fetches data, and writes one JSON artifact only
inside a caller-provided staging directory.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Final, Mapping, Sequence
from uuid import uuid4

from courtvision.sports.mlb.data.ballpark_factors import normalize_venue_name
from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessReport,
    HistoricalBacktestReadinessVerdict,
    audit_historical_backtest_readiness,
)
from courtvision.sports.mlb.data.historical_input_pack import (
    HistoricalInputPackValidationResult,
    historical_input_pack_paths,
    preflight_historical_input_pack,
)
from courtvision.sports.mlb.data.odds_snapshot_ingestion import (
    american_to_decimal,
    american_to_implied_probability,
)
from courtvision.sports.mlb.data_manifest import compute_file_sha256
from courtvision.sports.mlb.training.hr_feature_allowlist import (
    MLBHRFeatureAvailability,
    MLBHRFeaturePackRow,
    MLBHRResearchFeaturePack,
    validate_mlb_hr_feature_names,
    validate_mlb_hr_feature_pack,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    LABEL_CUSTODY_FILENAME,
    MLBHRLabelCustodyError,
    assert_model_visible_feature_pack_label_free,
    build_label_custody_payload,
    validate_mlb_hr_label_custody,
)


HISTORICAL_FEATURE_PACK_VERSION: Final = "mlb-hr-research-feature-pack-v1"
HISTORICAL_FEATURE_PACK_FILENAME: Final = "mlb_hr_research_feature_pack.json"
HITTER_RECENT_PA_LIMIT: Final = 50
PITCHER_RECENT_BF_LIMIT: Final = 100
MARKET_COVERAGE_SEGMENT_NAME: Final = "market_coverage"
MARKET_COVERED_SEGMENT: Final = "market_covered"
MARKET_MISSING_SEGMENT: Final = "market_missing"
INSUFFICIENT_HISTORICAL_LOOKBACK: Final = "insufficient_historical_lookback"

PREGAME_FEATURE_NAMES: Final = (
    "batter_hand",
    "pitcher_hand",
    "platoon_side",
    "weather_temperature",
    "weather_wind_speed",
    "weather_wind_direction",
    "weather_wind_out_to_field",
    "weather_humidity",
    "roof_status",
    "park_factor_hr",
    "park_factor_lhb",
    "park_factor_rhb",
    "altitude",
)
ROLLING_FEATURE_NAMES: Final = (
    "hitter_pa_window",
    "hitter_recent_hr_rate",
    "hitter_recent_barrel_rate",
    "hitter_recent_hard_hit_rate",
    "hitter_recent_fly_ball_rate",
    "hitter_avg_exit_velocity",
    "hitter_max_exit_velocity",
    "hitter_season_hr_rate_to_date",
    "hitter_season_barrel_rate_to_date",
    "hitter_season_hard_hit_rate_to_date",
    "pitcher_batters_faced_window",
    "pitcher_hr_allowed_rate_to_date",
    "pitcher_barrel_allowed_rate_to_date",
    "pitcher_hard_hit_allowed_rate_to_date",
    "pitcher_fly_ball_allowed_rate_to_date",
    "pitcher_pitch_mix_json",
)
MARKET_FEATURE_NAMES: Final = (
    "sportsbook",
    "odds_provider",
    "hr_market_available",
    "american_odds",
    "decimal_odds",
    "implied_probability",
    "odds_collected_at",
    "odds_as_of",
    "odds_is_fresh_for_pregame",
)
FEATURE_NAMES: Final = (
    *PREGAME_FEATURE_NAMES,
    *ROLLING_FEATURE_NAMES,
    *MARKET_FEATURE_NAMES,
)

_WEATHER_FEATURE_SOURCES: Final[Mapping[str, str]] = {
    "weather_temperature": "temperature",
    "weather_wind_speed": "wind_speed",
    "weather_wind_direction": "wind_direction",
    "weather_wind_out_to_field": "wind_out_to_field",
    "weather_humidity": "humidity",
    "roof_status": "roof_status",
}
_BALLPARK_FEATURE_SOURCES: Final[Mapping[str, str]] = {
    "park_factor_hr": "park_factor_hr",
    "park_factor_lhb": "handedness_factor_lhb",
    "park_factor_rhb": "handedness_factor_rhb",
    "altitude": "altitude",
}
_HITTER_ROLLING_FEATURES: Final = frozenset(
    name for name in ROLLING_FEATURE_NAMES if name.startswith("hitter_")
)
_PITCHER_ROLLING_FEATURES: Final = frozenset(
    name for name in ROLLING_FEATURE_NAMES if name.startswith("pitcher_")
)
_FORBIDDEN_STAGING_PARTS: Final = frozenset(
    {
        "cache",
        "caches",
        "history",
        "manual",
        "manualdata",
        "output",
        "outputs",
        "pytestcache",
        "pycache",
        "runtime",
        "testoutputs",
    }
)


class HistoricalFeaturePackBuildError(ValueError):
    """Raised when a staged pack cannot safely produce a feature pack."""


@dataclass(frozen=True, slots=True)
class HistoricalFeaturePackBuildResult:
    """Separate finalized feature and label-custody artifacts."""

    output_dir: Path
    feature_pack_path: Path
    label_custody_path: Path
    preflight: HistoricalInputPackValidationResult
    readiness: HistoricalBacktestReadinessReport
    feature_pack: MLBHRResearchFeaturePack
    population_accounting: Mapping[str, object]

    @property
    def row_count(self) -> int:
        return len(self.feature_pack.rows)


@dataclass(frozen=True, slots=True)
class _CSVTable:
    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def _read_csv(path: Path) -> _CSVTable:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = tuple(reader.fieldnames or ())
            rows = tuple(dict(row) for row in reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise HistoricalFeaturePackBuildError(
            f"could not read staged source {path.name}: {exc}"
        ) from exc
    if not headers:
        raise HistoricalFeaturePackBuildError(
            f"staged source has no CSV header: {path.name}"
        )
    if len(headers) != len(set(headers)):
        raise HistoricalFeaturePackBuildError(
            f"staged source has duplicate CSV headers: {path.name}"
        )
    return _CSVTable(headers, rows)


def _text(row: Mapping[str, object], field_name: str) -> str:
    value = row.get(field_name)
    return "" if value is None else str(value).strip()


def _parse_date(value: object, label: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise HistoricalFeaturePackBuildError(
            f"{label} must be an ISO-8601 date"
        ) from exc


def _parse_aware_datetime(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise HistoricalFeaturePackBuildError(
            f"{label} must be an ISO-8601 datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistoricalFeaturePackBuildError(f"{label} must be timezone-aware")
    return parsed


def _optional_aware_datetime(value: object, label: str) -> datetime | None:
    if value is None:
        return None
    return _parse_aware_datetime(value, label)


def _optional_float(value: object) -> float | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 8) if denominator else None


def _normalized_event(row: Mapping[str, object]) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _text(row, "events").casefold()).strip("_")


def _terminal_rows(rows: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [row for row in rows if _text(row, "events")]


def _batted_rows(rows: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [
        row
        for row in rows
        if _text(row, "bb_type") or _optional_float(row.get("launch_speed")) is not None
    ]


def _barrel_rate(rows: Sequence[Mapping[str, object]]) -> float | None:
    usable = [row for row in rows if _text(row, "barrel")]
    return _rate(
        sum(_text(row, "barrel").casefold() in {"1", "true", "yes"} for row in usable),
        len(usable),
    )


def _hard_hit_rate(rows: Sequence[Mapping[str, object]]) -> float | None:
    speeds = [
        value
        for row in rows
        if (value := _optional_float(row.get("launch_speed"))) is not None
    ]
    return _rate(sum(value >= 95.0 for value in speeds), len(speeds))


def _fly_ball_rate(rows: Sequence[Mapping[str, object]]) -> float | None:
    usable = [row for row in rows if _text(row, "bb_type")]
    return _rate(
        sum(_text(row, "bb_type").casefold() == "fly_ball" for row in usable),
        len(usable),
    )


def _exit_velocities(rows: Sequence[Mapping[str, object]]) -> list[float]:
    return [
        value
        for row in rows
        if (value := _optional_float(row.get("launch_speed"))) is not None
    ]


def _is_strictly_before(source_date: date, target_date: date) -> bool:
    """Single testable boundary for the no-same-day-history invariant."""

    return source_date < target_date


def _historical_available_at(source_date: date) -> datetime:
    """Return a conservative availability time after all games on a source date.

    Staged Statcast rows have dates but no completion timestamps. Noon UTC on
    the following date intentionally fails closed for unusually early next-day
    odds snapshots instead of claiming same-night availability without proof.
    """

    return datetime.combine(
        source_date + timedelta(days=1),
        time(12, 0),
        tzinfo=timezone.utc,
    )


def _history_before(
    rows: Sequence[dict[str, str]],
    *,
    id_field: str,
    identity: str,
    target_date: date,
) -> list[dict[str, str]]:
    history = [
        row
        for row in rows
        if _text(row, id_field) == identity
        and _is_strictly_before(
            _parse_date(row.get("game_date"), "statcast.game_date"), target_date
        )
    ]
    return sorted(
        history,
        key=lambda row: (
            _parse_date(row.get("game_date"), "statcast.game_date"),
            _text(row, "game_pk"),
            _text(row, "inning"),
        ),
    )


def _latest_source_date(rows: Sequence[Mapping[str, object]]) -> date:
    return max(
        _parse_date(row.get("game_date"), "statcast.game_date") for row in rows
    )


def _latest_nonempty(rows: Sequence[Mapping[str, object]], field: str) -> str | None:
    return next((_text(row, field) for row in reversed(rows) if _text(row, field)), None)


def _hitter_features(
    history: Sequence[dict[str, str]], target_date: date
) -> dict[str, object]:
    terminal = _terminal_rows(history)
    recent = terminal[-HITTER_RECENT_PA_LIMIT:]
    recent_batted = _batted_rows(recent)
    season = [
        row
        for row in terminal
        if _parse_date(row.get("game_date"), "statcast.game_date").year
        == target_date.year
    ]
    season_batted = _batted_rows(season)
    exit_velocities = _exit_velocities(recent_batted)
    return {
        "hitter_pa_window": len(recent),
        "hitter_recent_hr_rate": _rate(
            sum(_normalized_event(row) == "home_run" for row in recent), len(recent)
        ),
        "hitter_recent_barrel_rate": _barrel_rate(recent_batted),
        "hitter_recent_hard_hit_rate": _hard_hit_rate(recent_batted),
        "hitter_recent_fly_ball_rate": _fly_ball_rate(recent_batted),
        "hitter_avg_exit_velocity": (
            round(sum(exit_velocities) / len(exit_velocities), 6)
            if exit_velocities
            else None
        ),
        "hitter_max_exit_velocity": max(exit_velocities, default=None),
        "hitter_season_hr_rate_to_date": _rate(
            sum(_normalized_event(row) == "home_run" for row in season), len(season)
        ),
        "hitter_season_barrel_rate_to_date": _barrel_rate(season_batted),
        "hitter_season_hard_hit_rate_to_date": _hard_hit_rate(season_batted),
    }


def _pitcher_features(
    history: Sequence[dict[str, str]], target_date: date
) -> dict[str, object]:
    terminal = _terminal_rows(history)
    recent = terminal[-PITCHER_RECENT_BF_LIMIT:]
    season_all = [
        row
        for row in history
        if _parse_date(row.get("game_date"), "statcast.game_date").year
        == target_date.year
    ]
    season_terminal = _terminal_rows(season_all)
    season_batted = _batted_rows(season_terminal)
    pitch_counts: dict[str, int] = {}
    for row in season_all:
        pitch_type = _text(row, "pitch_type")
        if pitch_type:
            pitch_counts[pitch_type] = pitch_counts.get(pitch_type, 0) + 1
    pitch_total = sum(pitch_counts.values())
    pitch_mix = {
        pitch_type: round(count / pitch_total, 8)
        for pitch_type, count in sorted(pitch_counts.items())
    }
    return {
        "pitcher_batters_faced_window": len(recent),
        "pitcher_hr_allowed_rate_to_date": _rate(
            sum(_normalized_event(row) == "home_run" for row in season_terminal),
            len(season_terminal),
        ),
        "pitcher_barrel_allowed_rate_to_date": _barrel_rate(season_batted),
        "pitcher_hard_hit_allowed_rate_to_date": _hard_hit_rate(season_batted),
        "pitcher_fly_ball_allowed_rate_to_date": _fly_ball_rate(season_batted),
        "pitcher_pitch_mix_json": json.dumps(
            pitch_mix, sort_keys=True, separators=(",", ":")
        ),
    }


def _validate_output_directory(path: str | Path) -> tuple[Path, bool]:
    output_dir = Path(path).expanduser().resolve()
    normalized_parts = {
        re.sub(r"[^a-z0-9]+", "", part.casefold()) for part in output_dir.parts
    }
    if normalized_parts & _FORBIDDEN_STAGING_PARTS:
        raise HistoricalFeaturePackBuildError(
            "feature staging output cannot be inside manual-data, output, history, "
            f"runtime, or cache folders: {output_dir}"
        )
    if output_dir.exists():
        if not output_dir.is_dir():
            raise HistoricalFeaturePackBuildError(
                f"output staging path is not a directory: {output_dir}"
            )
        if any(output_dir.iterdir()):
            raise HistoricalFeaturePackBuildError(
                f"output staging directory must be empty: {output_dir}"
            )
        return output_dir, False
    if not output_dir.parent.is_dir():
        raise HistoricalFeaturePackBuildError(
            f"output staging parent directory does not exist: {output_dir.parent}"
        )
    return output_dir, True


def _read_tables(pack_dir: Path) -> dict[str, _CSVTable]:
    paths = historical_input_pack_paths(pack_dir)
    return {
        source_name: _read_csv(source_path)
        for source_name, source_path in paths.source_map().items()
    }


def _indexes(
    tables: Mapping[str, _CSVTable],
) -> tuple[
    dict[tuple[str, date], dict[str, str]],
    dict[tuple[str, date, str], dict[str, str]],
    dict[tuple[str, date], dict[str, str]],
    dict[str, dict[str, str]],
]:
    games = {
        (_text(row, "game_id"), _parse_date(row.get("game_date"), "game.game_date")): row
        for row in tables["retrosheet_games"].rows
    }
    events = {
        (
            _text(row, "game_id"),
            _parse_date(row.get("game_date"), "event.game_date"),
            _text(row, "batter_id"),
        ): row
        for row in tables["retrosheet_events"].rows
    }
    weather = {
        (
            _text(row, "game_id"),
            _parse_date(row.get("game_date"), "weather.game_date"),
        ): row
        for row in tables["weather"].rows
    }
    ballparks = {
        normalize_venue_name(_text(row, "venue_name")): row
        for row in tables["ballpark_factors"].rows
    }
    return games, events, weather, ballparks


def _row_id(
    *, game_date: date, game_id: str, player_id: str, market_identity: str
) -> str:
    book_hash = hashlib.sha256(
        market_identity.casefold().encode("utf-8")
    ).hexdigest()[:12]
    return f"{game_date.isoformat()}:{game_id}:{player_id}:{book_hash}"


def _lineage(
    *,
    hitter_date: date,
    pitcher_date: date,
    weather_date: date,
    weather_at: datetime,
    park_date: date,
    park_at: datetime,
    feature_cutoff_at: datetime,
) -> tuple[MLBHRFeatureAvailability, ...]:
    values: list[MLBHRFeatureAvailability] = []
    for name in FEATURE_NAMES:
        if name in _HITTER_ROLLING_FEATURES:
            values.append(
                MLBHRFeatureAvailability(
                    name, _historical_available_at(hitter_date), hitter_date
                )
            )
        elif name in _PITCHER_ROLLING_FEATURES:
            values.append(
                MLBHRFeatureAvailability(
                    name, _historical_available_at(pitcher_date), pitcher_date
                )
            )
        elif name == "batter_hand":
            values.append(
                MLBHRFeatureAvailability(
                    name, _historical_available_at(hitter_date), hitter_date
                )
            )
        elif name == "pitcher_hand":
            values.append(
                MLBHRFeatureAvailability(
                    name, _historical_available_at(pitcher_date), pitcher_date
                )
            )
        elif name == "platoon_side":
            latest = max(hitter_date, pitcher_date)
            values.append(
                MLBHRFeatureAvailability(name, _historical_available_at(latest), latest)
            )
        elif name in _WEATHER_FEATURE_SOURCES:
            values.append(MLBHRFeatureAvailability(name, weather_at, weather_date))
        elif name in _BALLPARK_FEATURE_SOURCES:
            values.append(MLBHRFeatureAvailability(name, park_at, park_date))
        else:
            values.append(MLBHRFeatureAvailability(name, feature_cutoff_at))
    return tuple(values)


def _build_rows(
    tables: Mapping[str, _CSVTable],
) -> tuple[
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
    MLBHRResearchFeaturePack,
    dict[str, object],
]:
    games, events, weather_by_game, ballparks = _indexes(tables)
    statcast = tables["statcast"].rows
    artifact_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    firewall_rows: list[MLBHRFeaturePackRow] = []
    odds_by_player_game: dict[
        tuple[str, date, str], list[dict[str, str]]
    ] = {}
    for odds in tables["odds_snapshot"].rows:
        key = (
            _text(odds, "game_id"),
            _parse_date(odds.get("game_date"), "odds.game_date"),
            _text(odds, "player_id"),
        )
        odds_by_player_game.setdefault(key, []).append(odds)
    for matches in odds_by_player_game.values():
        matches.sort(key=lambda row: _text(row, "sportsbook").casefold())

    exclusion_count = 0
    missing_hitter_lookback_count = 0
    missing_pitcher_lookback_count = 0
    missing_both_lookback_count = 0
    eligible_batter_game_count = 0
    market_covered_batter_game_count = 0
    market_missing_batter_game_count = 0

    # Retrosheet's canonical labeled batter-game set is the population driver.
    # Odds are deliberately attached only after eligibility is established.
    for player_key, event in sorted(
        events.items(),
        key=lambda item: (item[0][1], item[0][0], item[0][2]),
    ):
        game_id, game_date, player_id = player_key
        game_key = (game_id, game_date)
        game = games.get(game_key)
        weather = weather_by_game.get(game_key)
        if game is None or event is None or weather is None:
            raise HistoricalFeaturePackBuildError(
                f"validated pack lost exact context for player-game {player_key}"
            )
        raw_label = _text(event, "is_home_run").casefold()
        if raw_label not in {"true", "false"}:
            raise HistoricalFeaturePackBuildError(
                f"player-game {player_key} has no explicit boolean HR label"
            )
        is_home_run = raw_label == "true"
        venue_key = normalize_venue_name(_text(game, "venue_name"))
        ballpark = ballparks.get(venue_key)
        if ballpark is None:
            raise HistoricalFeaturePackBuildError(
                f"validated pack lost ballpark context for {player_key}"
            )

        event_start = _parse_aware_datetime(
            weather.get("event_start_time"), "weather.event_start_time"
        )
        weather_at = _parse_aware_datetime(
            weather.get("collected_at"), "weather.collected_at"
        )
        weather_date = _parse_date(weather.get("as_of_date"), "weather.as_of_date")
        park_at = _parse_aware_datetime(
            ballpark.get("collected_at"), "ballpark.collected_at"
        )
        park_date = _parse_date(ballpark.get("as_of_date"), "ballpark.as_of_date")
        if weather_date > game_date:
            raise HistoricalFeaturePackBuildError(
                f"weather feature source date is after game_date for {player_key}"
            )
        if park_date > game_date:
            raise HistoricalFeaturePackBuildError(
                f"ballpark feature source date is after game_date for {player_key}"
            )

        pitcher_id = _text(event, "pitcher_id")
        hitter_history = _history_before(
            statcast,
            id_field="batter",
            identity=player_id,
            target_date=game_date,
        )
        pitcher_history = _history_before(
            statcast,
            id_field="pitcher",
            identity=pitcher_id,
            target_date=game_date,
        )
        missing_hitter_lookback = not _terminal_rows(hitter_history)
        missing_pitcher_lookback = not _terminal_rows(pitcher_history)
        if missing_hitter_lookback or missing_pitcher_lookback:
            # A rolling feature row cannot honestly be created until both sides
            # have at least one strictly prior dated terminal event.  This is an
            # explicit population exclusion, never an odds-driven row loss.
            exclusion_count += 1
            missing_hitter_lookback_count += int(missing_hitter_lookback)
            missing_pitcher_lookback_count += int(missing_pitcher_lookback)
            missing_both_lookback_count += int(
                missing_hitter_lookback and missing_pitcher_lookback
            )
            continue

        eligible_batter_game_count += 1
        hitter_date = _latest_source_date(hitter_history)
        pitcher_date = _latest_source_date(pitcher_history)
        batter_hand = _latest_nonempty(hitter_history, "stand")
        pitcher_hand = _latest_nonempty(pitcher_history, "p_throws")
        if not batter_hand or not pitcher_hand:
            raise HistoricalFeaturePackBuildError(
                f"prior-date handedness context is unavailable for {player_key}"
            )

        common_values: dict[str, object] = {
            "batter_hand": batter_hand,
            "pitcher_hand": pitcher_hand,
            "platoon_side": (
                "same_side" if batter_hand == pitcher_hand else "opposite_side"
            ),
            **{
                feature_name: (
                    _optional_float(weather.get(source_name))
                    if feature_name
                    in {"weather_temperature", "weather_wind_speed", "weather_humidity"}
                    else _text(weather, source_name) or None
                )
                for feature_name, source_name in _WEATHER_FEATURE_SOURCES.items()
            },
            **{
                feature_name: _optional_float(ballpark.get(source_name))
                for feature_name, source_name in _BALLPARK_FEATURE_SOURCES.items()
            },
            **_hitter_features(hitter_history, game_date),
            **_pitcher_features(pitcher_history, game_date),
        }

        odds_matches = odds_by_player_game.get(player_key, [])
        if odds_matches:
            market_covered_batter_game_count += 1
            joined_markets: Sequence[dict[str, str] | None] = odds_matches
        else:
            market_missing_batter_game_count += 1
            joined_markets = (None,)

        for odds in joined_markets:
            if odds is None:
                sportsbook = None
                odds_provider = None
                american_odds = None
                decimal_odds = None
                implied_probability = None
                odds_at = None
                odds_is_fresh = False
                market_available = False
                market_segment = MARKET_MISSING_SEGMENT
                market_identity = "__market_missing__"
                # This is lineage metadata only.  It is deliberately separate
                # from odds_collected_at so a missing market never receives a
                # fabricated odds timestamp.
                feature_cutoff_at = event_start - timedelta(microseconds=1)
            else:
                sportsbook = _text(odds, "sportsbook")
                odds_provider = _text(odds, "provider") or None
                odds_at = _parse_aware_datetime(
                    odds.get("odds_collected_at"), "odds.odds_collected_at"
                )
                try:
                    american_odds = int(float(_text(odds, "american_odds")))
                    decimal_odds = round(american_to_decimal(american_odds), 8)
                    implied_probability = round(
                        american_to_implied_probability(american_odds), 8
                    )
                except (ValueError, TypeError) as exc:
                    raise HistoricalFeaturePackBuildError(
                        f"invalid American odds for {player_key}"
                    ) from exc
                age = event_start - odds_at
                odds_is_fresh = timedelta(0) < age <= timedelta(hours=24)
                market_available = True
                market_segment = MARKET_COVERED_SEGMENT
                market_identity = sportsbook
                feature_cutoff_at = odds_at

            values: dict[str, object] = {
                **common_values,
                "sportsbook": sportsbook,
                "odds_provider": odds_provider,
                "hr_market_available": market_available,
                "american_odds": american_odds,
                "decimal_odds": decimal_odds,
                "implied_probability": implied_probability,
                "odds_collected_at": odds_at.isoformat() if odds_at else None,
                "odds_as_of": odds_at.isoformat() if odds_at else None,
                "odds_is_fresh_for_pregame": odds_is_fresh,
            }
            if tuple(values) != FEATURE_NAMES:
                raise HistoricalFeaturePackBuildError(
                    "internal feature schema order does not match the frozen allowlist"
                )

            row_id = _row_id(
                game_date=game_date,
                game_id=game_id,
                player_id=player_id,
                market_identity=market_identity,
            )
            lineage = _lineage(
                hitter_date=hitter_date,
                pitcher_date=pitcher_date,
                weather_date=weather_date,
                weather_at=weather_at,
                park_date=park_date,
                park_at=park_at,
                feature_cutoff_at=feature_cutoff_at,
            )
            firewall_row = MLBHRFeaturePackRow(
                row_id=row_id,
                game_date=game_date,
                odds_collected_at=odds_at,
                event_start_time=event_start,
                feature_availability=lineage,
                feature_cutoff_at=feature_cutoff_at,
            )
            firewall_rows.append(firewall_row)
            label_rows.append({"row_id": row_id, "is_home_run": is_home_run})
            artifact_rows.append(
                {
                    "row_id": row_id,
                    "game_id": game_id,
                    "game_date": game_date.isoformat(),
                    "player_id": player_id,
                    "player_name": _text(event, "batter_name"),
                    "feature_cutoff_at": feature_cutoff_at.isoformat(),
                    "odds_collected_at": odds_at.isoformat() if odds_at else None,
                    "event_start_time": event_start.isoformat(),
                    "segments": {MARKET_COVERAGE_SEGMENT_NAME: market_segment},
                    "feature_values": values,
                    "feature_availability": [
                        {
                            "feature_name": item.feature_name,
                            "available_at": (
                                item.available_at.isoformat()
                                if isinstance(item.available_at, datetime)
                                else item.available_at
                            ),
                            "source_latest_game_date": (
                                item.source_latest_game_date.isoformat()
                                if isinstance(item.source_latest_game_date, date)
                                else item.source_latest_game_date
                            ),
                        }
                        for item in lineage
                    ],
                }
            )

    feature_pack = MLBHRResearchFeaturePack(
        feature_names=FEATURE_NAMES,
        rows=tuple(firewall_rows),
    )
    firewall = validate_mlb_hr_feature_pack(feature_pack)
    if not firewall.is_valid:
        raise HistoricalFeaturePackBuildError(
            "feature firewall rejected staged build: " + "; ".join(firewall.errors)
        )
    target_population_count = len(events)
    population_accounting: dict[str, object] = {
        "unit": "canonical_labeled_batter_game",
        "target_population_definition": (
            "one unique validated Retrosheet game/date/batter label identity"
        ),
        "target_population_count": target_population_count,
        "eligible_batter_game_count": eligible_batter_game_count,
        "excluded_batter_game_count": exclusion_count,
        "exclusion_counts": {
            INSUFFICIENT_HISTORICAL_LOOKBACK: exclusion_count,
        },
        "exclusion_details": {
            "missing_hitter_prior_terminal_event": missing_hitter_lookback_count,
            "missing_pitcher_prior_terminal_event": missing_pitcher_lookback_count,
            "missing_both_prior_terminal_events": missing_both_lookback_count,
        },
        "exclusion_rules": {
            INSUFFICIENT_HISTORICAL_LOOKBACK: (
                "exclude when the batter or opposing pitcher has no strictly "
                "prior dated terminal Statcast event"
            ),
        },
        "market_covered_batter_game_count": market_covered_batter_game_count,
        "market_missing_batter_game_count": market_missing_batter_game_count,
        "odds_coverage_rate": (
            market_covered_batter_game_count / eligible_batter_game_count
            if eligible_batter_game_count
            else 0.0
        ),
        "feature_row_count": len(artifact_rows),
        "sportsbook_join_policy": (
            "left join all distinct validated sportsbook rows; emit one explicit "
            "market-missing row when no sportsbook matches; exact duplicate "
            "game/date/player/sportsbook snapshots fail input preflight"
        ),
    }
    if eligible_batter_game_count + exclusion_count != target_population_count:
        raise HistoricalFeaturePackBuildError(
            "internal population accounting does not reconcile"
        )
    return (
        tuple(artifact_rows),
        tuple(label_rows),
        feature_pack,
        population_accounting,
    )


def _artifact_payload(
    *,
    pack_dir: Path,
    input_manifest_sha256: str,
    rows: Sequence[Mapping[str, object]],
    population_accounting: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": HISTORICAL_FEATURE_PACK_VERSION,
        "mode": "historical_research",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_pack": {
            "path": str(pack_dir),
            "input_manifest_sha256": input_manifest_sha256,
        },
        "readiness_verdict": (
            HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
        ),
        "feature_names": list(FEATURE_NAMES),
        "rolling_policy": {
            "source_date_rule": "source_latest_game_date < game_date",
            "hitter_recent_pa_limit": HITTER_RECENT_PA_LIMIT,
            "pitcher_recent_bf_limit": PITCHER_RECENT_BF_LIMIT,
            "historical_available_at_policy": "source_game_date_plus_1_day_12:00_utc",
        },
        "population": dict(population_accounting),
        "missing_market_feature_cutoff_policy": (
            "event_start_time_minus_1_microsecond_lineage_only"
        ),
        "rows": list(rows),
        "feature_firewall_valid": True,
        "approval_status": "not_approved",
        "model_training_enabled": False,
        "backtesting_enabled": False,
        "predictions_enabled": False,
        "eligible_for_betting": False,
        "ev_enabled": False,
        "kelly_eligible": False,
        "elite_enabled": False,
        "staking_enabled": False,
    }


def feature_pack_from_payload(payload: Mapping[str, object]) -> MLBHRResearchFeaturePack:
    """Reconstruct and validate the firewall declaration in an artifact payload."""

    if payload.get("schema_version") != HISTORICAL_FEATURE_PACK_VERSION:
        raise HistoricalFeaturePackBuildError("unsupported feature-pack schema version")
    raw_names = payload.get("feature_names")
    if not isinstance(raw_names, list) or not all(
        isinstance(name, str) for name in raw_names
    ):
        raise HistoricalFeaturePackBuildError("feature_names must be a list of strings")
    names = tuple(raw_names)
    name_result = validate_mlb_hr_feature_names(names)
    if not name_result.is_valid:
        raise HistoricalFeaturePackBuildError(
            "feature names failed firewall: " + "; ".join(name_result.errors)
        )
    try:
        assert_model_visible_feature_pack_label_free(payload)
    except MLBHRLabelCustodyError as exc:
        raise HistoricalFeaturePackBuildError(str(exc)) from exc

    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        raise HistoricalFeaturePackBuildError("feature-pack rows must be a list")
    rows: list[MLBHRFeaturePackRow] = []
    for row_index, raw_row in enumerate(raw_rows):
        if not isinstance(raw_row, Mapping):
            raise HistoricalFeaturePackBuildError(
                f"feature-pack rows[{row_index}] must be an object"
            )
        raw_values = raw_row.get("feature_values")
        if (
            not isinstance(raw_values, Mapping)
            or len(raw_values) != len(names)
            or set(raw_values) != set(names)
        ):
            raise HistoricalFeaturePackBuildError(
                f"feature-pack rows[{row_index}] values do not match feature_names"
            )
        market_available = raw_values.get("hr_market_available")
        if not isinstance(market_available, bool):
            raise HistoricalFeaturePackBuildError(
                f"feature-pack rows[{row_index}].hr_market_available must be boolean"
            )
        market_fields = (
            "sportsbook",
            "odds_provider",
            "american_odds",
            "decimal_odds",
            "implied_probability",
            "odds_collected_at",
            "odds_as_of",
        )
        if not market_available:
            populated_market_fields = [
                field_name
                for field_name in market_fields
                if raw_values.get(field_name) is not None
            ]
            if populated_market_fields or raw_row.get("odds_collected_at") is not None:
                raise HistoricalFeaturePackBuildError(
                    f"feature-pack rows[{row_index}] fabricates odds fields while "
                    "hr_market_available is false: "
                    + ", ".join(populated_market_fields)
                )
            if raw_values.get("odds_is_fresh_for_pregame") is not False:
                raise HistoricalFeaturePackBuildError(
                    f"feature-pack rows[{row_index}].odds_is_fresh_for_pregame "
                    "must be false when the market is missing"
                )
        elif raw_row.get("odds_collected_at") is None:
            raise HistoricalFeaturePackBuildError(
                f"feature-pack rows[{row_index}].odds_collected_at is required "
                "when the market is available"
            )
        raw_segments = raw_row.get("segments")
        if raw_segments is not None:
            expected_segment = (
                MARKET_COVERED_SEGMENT
                if market_available
                else MARKET_MISSING_SEGMENT
            )
            if (
                not isinstance(raw_segments, Mapping)
                or raw_segments.get(MARKET_COVERAGE_SEGMENT_NAME)
                != expected_segment
            ):
                raise HistoricalFeaturePackBuildError(
                    f"feature-pack rows[{row_index}] has invalid market coverage segment"
                )
        raw_lineage = raw_row.get("feature_availability")
        if not isinstance(raw_lineage, list):
            raise HistoricalFeaturePackBuildError(
                f"feature-pack rows[{row_index}] lineage must be a list"
            )
        availability: list[MLBHRFeatureAvailability] = []
        for item in raw_lineage:
            if not isinstance(item, Mapping):
                raise HistoricalFeaturePackBuildError(
                    f"feature-pack rows[{row_index}] lineage entry must be an object"
                )
            feature_name = item.get("feature_name")
            if not isinstance(feature_name, str):
                raise HistoricalFeaturePackBuildError(
                    f"feature-pack rows[{row_index}] lineage feature_name must be text"
                )
            source_date_value = item.get("source_latest_game_date")
            availability.append(
                MLBHRFeatureAvailability(
                    feature_name=feature_name,
                    available_at=_parse_aware_datetime(
                        item.get("available_at"),
                        f"feature-pack rows[{row_index}] lineage available_at",
                    ),
                    source_latest_game_date=(
                        _parse_date(
                            source_date_value,
                            f"feature-pack rows[{row_index}] lineage source date",
                        )
                        if source_date_value is not None
                        else None
                    ),
                )
            )
        row_id = raw_row.get("row_id")
        if not isinstance(row_id, str):
            raise HistoricalFeaturePackBuildError(
                f"feature-pack rows[{row_index}].row_id must be text"
            )
        rows.append(
            MLBHRFeaturePackRow(
                row_id=row_id,
                game_date=_parse_date(
                    raw_row.get("game_date"), f"feature-pack rows[{row_index}].game_date"
                ),
                odds_collected_at=_optional_aware_datetime(
                    raw_row.get("odds_collected_at"),
                    f"feature-pack rows[{row_index}].odds_collected_at",
                ),
                event_start_time=_parse_aware_datetime(
                    raw_row.get("event_start_time"),
                    f"feature-pack rows[{row_index}].event_start_time",
                ),
                feature_availability=tuple(availability),
                feature_cutoff_at=_optional_aware_datetime(
                    raw_row.get("feature_cutoff_at"),
                    f"feature-pack rows[{row_index}].feature_cutoff_at",
                ),
            )
        )
    if payload.get("readiness_verdict") != (
        HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
    ):
        raise HistoricalFeaturePackBuildError(
            "feature-pack artifact is not READY_FOR_RESEARCH_BACKTEST"
        )
    if payload.get("feature_firewall_valid") is not True:
        raise HistoricalFeaturePackBuildError(
            "feature-pack artifact does not record a valid feature firewall"
        )
    if payload.get("approval_status") != "not_approved":
        raise HistoricalFeaturePackBuildError(
            "feature-pack artifact approval_status must remain not_approved"
        )
    disabled_gates = (
        "model_training_enabled",
        "backtesting_enabled",
        "predictions_enabled",
        "eligible_for_betting",
        "ev_enabled",
        "kelly_eligible",
        "elite_enabled",
        "staking_enabled",
    )
    invalid_gates = [name for name in disabled_gates if payload.get(name) is not False]
    if invalid_gates:
        raise HistoricalFeaturePackBuildError(
            "feature-pack artifact must explicitly disable gates: "
            + ", ".join(invalid_gates)
        )

    pack = MLBHRResearchFeaturePack(
        feature_names=names,
        rows=tuple(rows),
        mode=str(payload.get("mode", "")),
        approval_status=str(payload.get("approval_status", "")),
        model_training_enabled=False,
        backtesting_enabled=False,
        predictions_enabled=False,
        eligible_for_betting=False,
        ev_enabled=False,
        kelly_eligible=False,
        elite_enabled=False,
        staking_enabled=False,
    )
    result = validate_mlb_hr_feature_pack(pack)
    if not result.is_valid:
        raise HistoricalFeaturePackBuildError(
            "feature firewall rejected artifact: " + "; ".join(result.errors)
        )
    return pack


def load_historical_feature_pack(path: str | Path) -> MLBHRResearchFeaturePack:
    """Load one JSON feature artifact and rerun the feature firewall."""

    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HistoricalFeaturePackBuildError(
            f"could not read feature-pack artifact {source}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise HistoricalFeaturePackBuildError("feature-pack artifact must be an object")
    return feature_pack_from_payload(payload)


def build_historical_feature_pack(
    *,
    historical_input_pack: str | Path,
    output_staging_dir: str | Path,
) -> HistoricalFeaturePackBuildResult:
    """Build one firewall-valid feature pack from one READY staged input pack."""

    pack_dir = Path(historical_input_pack).expanduser().resolve()
    preflight = preflight_historical_input_pack(pack_dir)
    if not preflight.is_valid:
        raise HistoricalFeaturePackBuildError(
            "historical input-pack preflight failed: " + "; ".join(preflight.errors)
        )

    readiness = audit_historical_backtest_readiness(pack_dir)
    required_verdict = (
        HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
    )
    if readiness.verdict != required_verdict:
        details = (*readiness.blocking_reasons, *readiness.research_review_items)
        suffix = ": " + "; ".join(details) if details else ""
        raise HistoricalFeaturePackBuildError(
            f"feature build requires {required_verdict}; got {readiness.verdict}{suffix}"
        )
    if readiness.possible_leakage_columns:
        raise HistoricalFeaturePackBuildError(
            "readiness report contains possible leakage columns: "
            + ", ".join(readiness.possible_leakage_columns)
        )

    feature_name_result = validate_mlb_hr_feature_names(FEATURE_NAMES)
    if not feature_name_result.is_valid:
        raise HistoricalFeaturePackBuildError(
            "frozen feature schema failed firewall: "
            + "; ".join(feature_name_result.errors)
        )

    tables = _read_tables(pack_dir)
    artifact_rows, label_rows, feature_pack, population_accounting = _build_rows(tables)
    output_dir, create_output_dir = _validate_output_directory(output_staging_dir)
    output_path = output_dir / HISTORICAL_FEATURE_PACK_FILENAME
    custody_path = output_dir / LABEL_CUSTODY_FILENAME
    temporary_path: Path | None = None
    temporary_custody_path: Path | None = None
    created_output_dir = False
    succeeded = False
    try:
        if create_output_dir:
            output_dir.mkdir()
            created_output_dir = True
        temporary_path = output_dir / f".courtvision-feature-pack-{uuid4().hex}.json"
        temporary_custody_path = (
            output_dir / f".courtvision-label-custody-{uuid4().hex}.json"
        )
        created_at = datetime.now(timezone.utc).isoformat()
        payload = _artifact_payload(
            pack_dir=pack_dir,
            input_manifest_sha256=compute_file_sha256(preflight.paths.manifest),
            rows=artifact_rows,
            population_accounting=population_accounting,
        )
        payload["created_at"] = created_at
        assert_model_visible_feature_pack_label_free(payload)
        temporary_path.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        custody_payload = build_label_custody_payload(
            feature_payload=payload,
            feature_pack_sha256=compute_file_sha256(temporary_path),
            labels=label_rows,
            created_at=created_at,
        )
        temporary_custody_path.write_text(
            json.dumps(custody_payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        persisted = load_historical_feature_pack(temporary_path)
        if persisted != feature_pack:
            raise HistoricalFeaturePackBuildError(
                "persisted feature pack does not match the validated in-memory pack"
            )
        temporary_path.replace(output_path)
        temporary_custody_path.replace(custody_path)
        validate_mlb_hr_label_custody(
            feature_pack_path=output_path,
            label_custody_path=custody_path,
        )
        succeeded = True
        return HistoricalFeaturePackBuildResult(
            output_dir=output_dir,
            feature_pack_path=output_path,
            label_custody_path=custody_path,
            preflight=preflight,
            readiness=readiness,
            feature_pack=persisted,
            population_accounting=population_accounting,
        )
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        if temporary_custody_path is not None and temporary_custody_path.exists():
            temporary_custody_path.unlink()
        if not succeeded and output_path.exists():
            output_path.unlink()
        if not succeeded and custody_path.exists():
            custody_path.unlink()
        if not succeeded and created_output_dir and output_dir.exists():
            try:
                output_dir.rmdir()
            except OSError:
                pass


__all__ = [
    "FEATURE_NAMES",
    "HISTORICAL_FEATURE_PACK_FILENAME",
    "HISTORICAL_FEATURE_PACK_VERSION",
    "INSUFFICIENT_HISTORICAL_LOOKBACK",
    "MARKET_FEATURE_NAMES",
    "MARKET_COVERAGE_SEGMENT_NAME",
    "MARKET_COVERED_SEGMENT",
    "MARKET_MISSING_SEGMENT",
    "PREGAME_FEATURE_NAMES",
    "ROLLING_FEATURE_NAMES",
    "HistoricalFeaturePackBuildError",
    "HistoricalFeaturePackBuildResult",
    "LABEL_CUSTODY_FILENAME",
    "build_historical_feature_pack",
    "feature_pack_from_payload",
    "load_historical_feature_pack",
]
