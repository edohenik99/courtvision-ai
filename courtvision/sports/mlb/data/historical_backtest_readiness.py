"""Read-only readiness audit for staged MLB HR historical input packs.

This gate runs before model training or backtesting.  It inspects only the
fixed files in an existing historical input pack and never writes, fetches,
builds a dataset, trains a model, or changes any research/production gate.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Final, Mapping

from courtvision.sports.mlb.data.ballpark_factors import normalize_venue_name
from courtvision.sports.mlb.data.historical_input_pack import (
    PACK_SOURCE_FILES,
    historical_input_pack_paths,
    preflight_historical_input_pack,
)


# These floors are intentionally research-only.  They do not alter the
# downstream Phase 4E dataset-readiness thresholds or authorize model use.
MIN_REVIEW_LABELED_PLAYER_GAMES: Final = 2
MIN_REVIEW_UNIQUE_GAMES: Final = 1
MIN_REVIEW_UNIQUE_PLAYERS: Final = 2
MIN_REVIEW_UNIQUE_DATES: Final = 1

MIN_RESEARCH_LABELED_PLAYER_GAMES: Final = 1_000
MIN_RESEARCH_UNIQUE_GAMES: Final = 100
MIN_RESEARCH_UNIQUE_PLAYERS: Final = 100
MIN_RESEARCH_UNIQUE_DATES: Final = 30
MIN_RESEARCH_CALENDAR_SPAN_DAYS: Final = 30
MIN_RESEARCH_HR_POSITIVES: Final = 50
MIN_RESEARCH_HR_NEGATIVES: Final = 500
MIN_RESEARCH_ODDS_COVERAGE_RATE: Final = 0.80
MIN_RESEARCH_WEATHER_COVERAGE_RATE: Final = 0.95
MIN_RESEARCH_BALLPARK_COVERAGE_RATE: Final = 0.95


class HistoricalBacktestReadinessVerdict(str, Enum):
    """Strict staged-pack verdicts; all remain research-only."""

    NOT_READY = "NOT_READY"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    READY_FOR_RESEARCH_BACKTEST = "READY_FOR_RESEARCH_BACKTEST"


@dataclass(frozen=True, slots=True)
class HistoricalBacktestReadinessReport:
    """Immutable measurements and reasons for one staged-pack verdict."""

    pack_dir: Path
    verdict: str
    preflight_valid: bool
    source_row_counts: Mapping[str, int]
    labeled_player_game_rows: int
    unique_games: int
    unique_players: int
    unique_dates: int
    date_range_start: date | None
    date_range_end: date | None
    calendar_span_days: int
    missing_value_counts: Mapping[str, Mapping[str, int]]
    missing_value_count: int
    critical_missing_value_count: int
    duplicate_player_game_rows: int
    missing_label_count: int
    invalid_label_count: int
    hr_positive_count: int
    hr_negative_count: int
    hr_positive_rate: float
    odds_available_player_games: int
    odds_coverage_rate: float
    weather_complete_games: int
    weather_coverage_rate: float
    ballpark_complete_games: int
    ballpark_coverage_rate: float
    team_opponent_inconsistency_count: int
    possible_leakage_columns: tuple[str, ...] = field(default_factory=tuple)
    synthetic_identity_findings: tuple[str, ...] = field(default_factory=tuple)
    preflight_errors: tuple[str, ...] = field(default_factory=tuple)
    csv_errors: tuple[str, ...] = field(default_factory=tuple)
    blocking_reasons: tuple[str, ...] = field(default_factory=tuple)
    research_review_items: tuple[str, ...] = field(default_factory=tuple)
    approval_status: str = "not_approved"
    eligible_for_betting: bool = False
    kelly_eligible: bool = False
    model_training_enabled: bool = False
    backtesting_enabled: bool = False

    def __post_init__(self) -> None:
        valid_verdicts = {value.value for value in HistoricalBacktestReadinessVerdict}
        if self.verdict not in valid_verdicts:
            raise ValueError(
                f"unsupported historical readiness verdict: {self.verdict}"
            )
        row_counts = MappingProxyType(dict(self.source_row_counts))
        missing_counts = MappingProxyType(
            {
                source_name: MappingProxyType(dict(counts))
                for source_name, counts in self.missing_value_counts.items()
            }
        )
        object.__setattr__(self, "pack_dir", self.pack_dir.resolve())
        object.__setattr__(self, "source_row_counts", row_counts)
        object.__setattr__(self, "missing_value_counts", missing_counts)
        for name in (
            "possible_leakage_columns",
            "synthetic_identity_findings",
            "preflight_errors",
            "csv_errors",
            "blocking_reasons",
            "research_review_items",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.approval_status != "not_approved":
            raise ValueError("historical readiness must remain not_approved")
        if any(
            (
                self.eligible_for_betting,
                self.kelly_eligible,
                self.model_training_enabled,
                self.backtesting_enabled,
            )
        ):
            raise ValueError("historical readiness cannot enable execution gates")


@dataclass(frozen=True, slots=True)
class _CSVTable:
    headers: tuple[str, ...]
    rows: tuple[Mapping[str, str], ...]


_CRITICAL_FIELDS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "statcast": frozenset(
            {
                "game_date",
                "game_pk",
                "player_name",
                "batter",
                "pitcher",
                "description",
                "stand",
                "p_throws",
                "home_team",
                "away_team",
                "inning",
                "inning_topbot",
                "pitch_type",
            }
        ),
        "retrosheet_games": frozenset(
            {
                "game_id",
                "game_date",
                "home_team",
                "away_team",
                "game_number",
                "venue_name",
                "home_score",
                "away_score",
                "game_status",
                "source_type",
            }
        ),
        "retrosheet_events": frozenset(
            {
                "game_id",
                "game_date",
                "inning",
                "batting_team",
                "fielding_team",
                "batter_id",
                "batter_name",
                "pitcher_id",
                "pitcher_name",
                "event_type",
                "event_text",
                "is_home_run",
                "rbi",
                "source_type",
            }
        ),
        "weather": frozenset(
            {
                "game_id",
                "game_date",
                "event_start_time",
                "venue_name",
                "temperature",
                "wind_speed",
                "wind_direction",
                "source_name",
                "source_type",
                "collected_at",
                "as_of_date",
            }
        ),
        "ballpark_factors": frozenset(
            {
                "venue_name",
                "team",
                "park_factor_hr",
                "source_name",
                "source_type",
                "data_version",
                "collected_at",
                "as_of_date",
            }
        ),
        "odds_snapshot": frozenset(
            {
                "game_date",
                "game_id",
                "player_id",
                "player_name",
                "team",
                "opponent",
                "market_type",
                "sportsbook",
                "american_odds",
                "odds_collected_at",
                "event_start_time",
                "home_team",
                "away_team",
                "provider",
                "source_type",
            }
        ),
    }
)

_WEATHER_COMPLETE_FIELDS: Final = _CRITICAL_FIELDS["weather"]
_BALLPARK_COMPLETE_FIELDS: Final = _CRITICAL_FIELDS["ballpark_factors"]
_PROHIBITED_IDENTITY_TOKEN = re.compile(
    r"(?:^|\s)"
    r"(sample|fixture|mock|test|synthetic|dummy|fake|example|placeholder)"
    r"(?:$|\s)",
    re.IGNORECASE,
)
_SYNTHETIC_ID = re.compile(
    r"^(?:[bp]\d+|player[-_]?\d+|pitcher[-_]?\d+|batter[-_]?\d+)$",
    re.IGNORECASE,
)
_SYNTHETIC_TEAM = re.compile(r"^(?:EX[A-Z]|TST|AAA|BBB|XXX)$")
_LEAKAGE_EXACT_COLUMNS: Final = frozenset(
    {
        "actual",
        "actual_result",
        "backtest_profit",
        "bet_result",
        "closing_line",
        "closing_odds",
        "closing_price",
        "edge",
        "eligible_for_backtest",
        "eligible_for_betting",
        "eligible_for_training",
        "elite",
        "ev",
        "final_score",
        "future_outcome",
        "grade",
        "hit_hr_today",
        "home_run_count",
        "kelly",
        "kelly_fraction",
        "label",
        "label_available",
        "model_probability",
        "outcome",
        "payout",
        "postgame_result",
        "predicted_probability",
        "profit",
        "result",
        "roi",
        "settlement",
        "target",
        "wager_result",
    }
)
_LEAKAGE_PREFIXES: Final = (
    "actual_",
    "future_",
    "label_",
    "outcome_",
    "post_game_",
    "postgame_",
    "result_",
    "target_",
)
_LEAKAGE_SUFFIXES: Final = (
    "_grade",
    "_outcome",
    "_payout",
    "_profit",
    "_result",
    "_roi",
    "_target",
)


def _read_csv(path: Path) -> tuple[_CSVTable, str | None]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = tuple(reader.fieldnames or ())
            rows = tuple(dict(row) for row in reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        return _CSVTable((), ()), f"could not read {path.name}: {exc}"
    if not headers:
        return _CSVTable((), rows), f"{path.name} has no CSV header"
    if len(headers) != len(set(headers)):
        return _CSVTable(headers, rows), f"{path.name} has duplicate CSV headers"
    return _CSVTable(headers, rows), None


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _missing(value: object) -> bool:
    return not _text(value)


def _key(row: Mapping[str, object], *fields: str) -> tuple[str, ...] | None:
    values = tuple(_text(row.get(field_name)) for field_name in fields)
    return values if all(values) else None


def _parse_date(value: object) -> date | None:
    try:
        return date.fromisoformat(_text(value))
    except ValueError:
        return None


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _missing_counts(
    tables: Mapping[str, _CSVTable],
) -> tuple[dict[str, dict[str, int]], int, int]:
    by_source: dict[str, dict[str, int]] = {}
    total = 0
    critical_total = 0
    for source_name, table in tables.items():
        source_counts: dict[str, int] = {}
        for header in table.headers:
            count = sum(_missing(row.get(header)) for row in table.rows)
            if count:
                source_counts[header] = count
                total += count
                if header in _CRITICAL_FIELDS[source_name]:
                    critical_total += count
        by_source[source_name] = source_counts
    return by_source, total, critical_total


def _possible_leakage_columns(tables: Mapping[str, _CSVTable]) -> tuple[str, ...]:
    findings: list[str] = []
    for source_name, table in tables.items():
        for header in table.headers:
            normalized = re.sub(r"[^a-z0-9]+", "_", header.casefold()).strip("_")
            if (
                normalized in _LEAKAGE_EXACT_COLUMNS
                or normalized.startswith(_LEAKAGE_PREFIXES)
                or normalized.endswith(_LEAKAGE_SUFFIXES)
            ):
                findings.append(f"{source_name}.{header}")
    return tuple(sorted(set(findings)))


def _prohibited_identity_text(value: object) -> bool:
    normalized = re.sub(r"[^\w]+", " ", _text(value)).strip()
    return bool(
        normalized and _PROHIBITED_IDENTITY_TOKEN.search(normalized)
    )


def _synthetic_identity_findings(
    tables: Mapping[str, _CSVTable], manifest_path: Path
) -> tuple[str, ...]:
    findings: set[str] = set()
    identity_fields = {
        "statcast": (("batter", "player_name"), ("pitcher", None)),
        "retrosheet_events": (
            ("batter_id", "batter_name"),
            ("pitcher_id", "pitcher_name"),
        ),
        "odds_snapshot": (("player_id", "player_name"),),
    }
    for source_name, pairs in identity_fields.items():
        for row_number, row in enumerate(tables[source_name].rows, start=2):
            for id_field, name_field in pairs:
                identity_id = _text(row.get(id_field))
                identity_name = _text(row.get(name_field)) if name_field else ""
                if _SYNTHETIC_ID.fullmatch(identity_id) or (
                    name_field and _prohibited_identity_text(identity_name)
                ):
                    findings.add(
                        f"{source_name} row {row_number} has synthetic "
                        f"{id_field}/{name_field or 'identity'}"
                    )

    team_fields = {
        "statcast": ("home_team", "away_team"),
        "retrosheet_games": ("home_team", "away_team"),
        "retrosheet_events": ("batting_team", "fielding_team"),
        "ballpark_factors": ("team",),
        "odds_snapshot": ("team", "opponent", "home_team", "away_team"),
    }
    provenance_fields = {
        "weather": ("source_name", "source_type"),
        "ballpark_factors": ("source_name", "source_type"),
        "odds_snapshot": ("provider", "source_type"),
    }
    for source_name, fields in team_fields.items():
        for row_number, row in enumerate(tables[source_name].rows, start=2):
            for field_name in fields:
                if _SYNTHETIC_TEAM.fullmatch(_text(row.get(field_name))):
                    findings.add(
                        f"{source_name} row {row_number} has synthetic {field_name}"
                    )
    for source_name, fields in provenance_fields.items():
        for row_number, row in enumerate(tables[source_name].rows, start=2):
            for field_name in fields:
                if _prohibited_identity_text(row.get(field_name)):
                    findings.add(
                        f"{source_name} row {row_number} has sample/fixture "
                        f"{field_name}"
                    )

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, Mapping):
        sources = payload.get("sources")
        if isinstance(sources, list):
            for index, source in enumerate(sources):
                if isinstance(source, Mapping) and _prohibited_identity_text(
                    source.get("provider_label")
                ):
                    findings.add(
                        f"manifest source {index} has sample/fixture provider_label"
                    )
    return tuple(sorted(findings))


def _team_opponent_inconsistencies(tables: Mapping[str, _CSVTable]) -> int:
    count = 0
    games: dict[tuple[str, str], tuple[str, str]] = {}
    for row in tables["retrosheet_games"].rows:
        game_key = _key(row, "game_id", "game_date")
        teams = (_text(row.get("home_team")), _text(row.get("away_team")))
        if game_key is None or not all(teams) or teams[0] == teams[1]:
            count += 1
            continue
        games[game_key] = teams

    player_teams: dict[tuple[str, str, str], tuple[str, str]] = {}
    for row in tables["retrosheet_events"].rows:
        game_key = _key(row, "game_id", "game_date")
        player_key = _key(row, "game_id", "game_date", "batter_id")
        teams = (_text(row.get("batting_team")), _text(row.get("fielding_team")))
        expected = games.get(game_key) if game_key is not None else None
        if (
            expected is None
            or not all(teams)
            or teams[0] == teams[1]
            or set(teams) != set(expected)
        ):
            count += 1
        elif player_key is not None:
            previous = player_teams.setdefault(player_key, teams)
            if previous != teams:
                count += 1

    for row in tables["statcast"].rows:
        game_key = _key(row, "game_pk", "game_date")
        teams = (_text(row.get("home_team")), _text(row.get("away_team")))
        if game_key is None or games.get(game_key) != teams or teams[0] == teams[1]:
            count += 1

    for row in tables["odds_snapshot"].rows:
        game_key = _key(row, "game_id", "game_date")
        player_key = _key(row, "game_id", "game_date", "player_id")
        game_teams = (_text(row.get("home_team")), _text(row.get("away_team")))
        player_context = (_text(row.get("team")), _text(row.get("opponent")))
        if (
            game_key is None
            or games.get(game_key) != game_teams
            or player_key is None
            or player_teams.get(player_key) != player_context
            or player_context[0] == player_context[1]
        ):
            count += 1
    return count


def _context_coverage(
    tables: Mapping[str, _CSVTable],
    game_keys: set[tuple[str, str]],
) -> tuple[int, float, int, float]:
    complete_weather: set[tuple[str, str]] = set()
    for row in tables["weather"].rows:
        game_key = _key(row, "game_id", "game_date")
        if game_key is not None and all(
            not _missing(row.get(field_name))
            for field_name in _WEATHER_COMPLETE_FIELDS
        ):
            complete_weather.add(game_key)

    complete_venues = {
        normalized
        for row in tables["ballpark_factors"].rows
        if all(
            not _missing(row.get(field_name))
            for field_name in _BALLPARK_COMPLETE_FIELDS
        )
        and (normalized := normalize_venue_name(_text(row.get("venue_name"))))
    }
    complete_ballpark_games = {
        game_key
        for row in tables["retrosheet_games"].rows
        if (game_key := _key(row, "game_id", "game_date")) is not None
        and normalize_venue_name(_text(row.get("venue_name"))) in complete_venues
    }
    weather_count = len(game_keys & complete_weather)
    ballpark_count = len(game_keys & complete_ballpark_games)
    return (
        weather_count,
        _rate(weather_count, len(game_keys)),
        ballpark_count,
        _rate(ballpark_count, len(game_keys)),
    )


def _research_review_items(
    *,
    labeled_rows: int,
    unique_games: int,
    unique_players: int,
    unique_dates: int,
    calendar_span_days: int,
    positive_count: int,
    negative_count: int,
    odds_rate: float,
    weather_rate: float,
    ballpark_rate: float,
) -> tuple[str, ...]:
    checks = (
        (
            labeled_rows >= MIN_RESEARCH_LABELED_PLAYER_GAMES,
            f"labeled player-games require >= {MIN_RESEARCH_LABELED_PLAYER_GAMES}; "
            f"actual={labeled_rows}",
        ),
        (
            unique_games >= MIN_RESEARCH_UNIQUE_GAMES,
            f"unique games require >= {MIN_RESEARCH_UNIQUE_GAMES}; "
            f"actual={unique_games}",
        ),
        (
            unique_players >= MIN_RESEARCH_UNIQUE_PLAYERS,
            f"unique players require >= {MIN_RESEARCH_UNIQUE_PLAYERS}; "
            f"actual={unique_players}",
        ),
        (
            unique_dates >= MIN_RESEARCH_UNIQUE_DATES,
            f"unique dates require >= {MIN_RESEARCH_UNIQUE_DATES}; "
            f"actual={unique_dates}",
        ),
        (
            calendar_span_days >= MIN_RESEARCH_CALENDAR_SPAN_DAYS,
            f"calendar span requires >= {MIN_RESEARCH_CALENDAR_SPAN_DAYS} days; "
            f"actual={calendar_span_days}",
        ),
        (
            positive_count >= MIN_RESEARCH_HR_POSITIVES,
            f"HR-positive labels require >= {MIN_RESEARCH_HR_POSITIVES}; "
            f"actual={positive_count}",
        ),
        (
            negative_count >= MIN_RESEARCH_HR_NEGATIVES,
            f"HR-negative labels require >= {MIN_RESEARCH_HR_NEGATIVES}; "
            f"actual={negative_count}",
        ),
        (
            odds_rate >= MIN_RESEARCH_ODDS_COVERAGE_RATE,
            f"odds coverage requires >= {MIN_RESEARCH_ODDS_COVERAGE_RATE:.0%}; "
            f"actual={odds_rate:.1%}",
        ),
        (
            weather_rate >= MIN_RESEARCH_WEATHER_COVERAGE_RATE,
            f"weather completeness requires >= "
            f"{MIN_RESEARCH_WEATHER_COVERAGE_RATE:.0%}; actual={weather_rate:.1%}",
        ),
        (
            ballpark_rate >= MIN_RESEARCH_BALLPARK_COVERAGE_RATE,
            f"ballpark completeness requires >= "
            f"{MIN_RESEARCH_BALLPARK_COVERAGE_RATE:.0%}; actual={ballpark_rate:.1%}",
        ),
    )
    return tuple(message for passed, message in checks if not passed)


def audit_historical_backtest_readiness(
    pack_dir: str | Path,
) -> HistoricalBacktestReadinessReport:
    """Audit one staged pack in place without creating or changing any file."""

    paths = historical_input_pack_paths(pack_dir)
    preflight = preflight_historical_input_pack(paths.root)
    tables: dict[str, _CSVTable] = {}
    csv_errors: list[str] = []
    for source_name, source_path in paths.source_map().items():
        table, error = _read_csv(source_path)
        tables[source_name] = table
        if error:
            csv_errors.append(error)

    row_counts = {
        source_name: len(table.rows) for source_name, table in tables.items()
    }
    missing_counts, missing_total, critical_missing_total = _missing_counts(tables)
    leakage_columns = _possible_leakage_columns(tables)
    synthetic_findings = _synthetic_identity_findings(tables, paths.manifest)

    game_keys = {
        key
        for row in tables["retrosheet_games"].rows
        if (key := _key(row, "game_id", "game_date")) is not None
    }
    player_game_keys = [
        key
        for row in tables["retrosheet_events"].rows
        if (key := _key(row, "game_id", "game_date", "batter_id")) is not None
    ]
    unique_player_games = set(player_game_keys)
    duplicate_player_games = len(player_game_keys) - len(unique_player_games)
    unique_players = len({key[2] for key in unique_player_games})

    game_dates = {
        parsed
        for row in tables["retrosheet_games"].rows
        if (parsed := _parse_date(row.get("game_date"))) is not None
    }
    date_start = min(game_dates) if game_dates else None
    date_end = max(game_dates) if game_dates else None
    calendar_span_days = (
        (date_end - date_start).days + 1 if date_start and date_end else 0
    )

    missing_labels = 0
    invalid_labels = 0
    positive_count = 0
    negative_count = 0
    for row in tables["retrosheet_events"].rows:
        label = _text(row.get("is_home_run")).casefold()
        if not label:
            missing_labels += 1
        elif label in {"1", "true", "yes", "y"}:
            positive_count += 1
        elif label in {"0", "false", "no", "n"}:
            negative_count += 1
        else:
            invalid_labels += 1

    odds_player_games = {
        key
        for row in tables["odds_snapshot"].rows
        if (key := _key(row, "game_id", "game_date", "player_id")) is not None
    }
    odds_available = len(unique_player_games & odds_player_games)
    odds_rate = _rate(odds_available, len(unique_player_games))
    weather_count, weather_rate, ballpark_count, ballpark_rate = _context_coverage(
        tables, game_keys
    )
    team_inconsistencies = _team_opponent_inconsistencies(tables)

    blocking_reasons: list[str] = []
    if not preflight.is_valid:
        blocking_reasons.append("historical input pack preflight failed")
    if csv_errors:
        blocking_reasons.append("one or more staged CSV files could not be audited")
    if critical_missing_total:
        blocking_reasons.append(
            f"critical staged fields contain {critical_missing_total} missing values"
        )
    if missing_labels:
        blocking_reasons.append(f"{missing_labels} outcome labels are missing")
    if invalid_labels:
        blocking_reasons.append(f"{invalid_labels} outcome labels are invalid")
    if duplicate_player_games:
        blocking_reasons.append(
            f"{duplicate_player_games} duplicate player-game label rows were found"
        )
    if team_inconsistencies:
        blocking_reasons.append(
            f"{team_inconsistencies} team/opponent inconsistencies were found"
        )
    if leakage_columns:
        blocking_reasons.append(
            "possible leakage columns were found: " + ", ".join(leakage_columns)
        )
    if synthetic_findings:
        blocking_reasons.append(
            f"{len(synthetic_findings)} sample/synthetic identity findings were found"
        )

    review_floor_checks = (
        (
            len(unique_player_games) >= MIN_REVIEW_LABELED_PLAYER_GAMES,
            f"review requires >= {MIN_REVIEW_LABELED_PLAYER_GAMES} "
            "labeled player-games",
        ),
        (
            len(game_keys) >= MIN_REVIEW_UNIQUE_GAMES,
            f"review requires >= {MIN_REVIEW_UNIQUE_GAMES} unique game",
        ),
        (
            unique_players >= MIN_REVIEW_UNIQUE_PLAYERS,
            f"review requires >= {MIN_REVIEW_UNIQUE_PLAYERS} unique players",
        ),
        (
            len(game_dates) >= MIN_REVIEW_UNIQUE_DATES,
            f"review requires >= {MIN_REVIEW_UNIQUE_DATES} unique date",
        ),
        (positive_count > 0, "review requires at least one HR-positive label"),
        (negative_count > 0, "review requires at least one HR-negative label"),
    )
    blocking_reasons.extend(
        message for passed, message in review_floor_checks if not passed
    )
    blocking_reasons = list(dict.fromkeys(blocking_reasons))

    research_items = _research_review_items(
        labeled_rows=len(unique_player_games),
        unique_games=len(game_keys),
        unique_players=unique_players,
        unique_dates=len(game_dates),
        calendar_span_days=calendar_span_days,
        positive_count=positive_count,
        negative_count=negative_count,
        odds_rate=odds_rate,
        weather_rate=weather_rate,
        ballpark_rate=ballpark_rate,
    )
    if blocking_reasons:
        verdict = HistoricalBacktestReadinessVerdict.NOT_READY
    elif research_items:
        verdict = HistoricalBacktestReadinessVerdict.READY_FOR_REVIEW
    else:
        verdict = HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST

    return HistoricalBacktestReadinessReport(
        pack_dir=paths.root,
        verdict=verdict.value,
        preflight_valid=preflight.is_valid,
        source_row_counts=row_counts,
        labeled_player_game_rows=len(unique_player_games),
        unique_games=len(game_keys),
        unique_players=unique_players,
        unique_dates=len(game_dates),
        date_range_start=date_start,
        date_range_end=date_end,
        calendar_span_days=calendar_span_days,
        missing_value_counts=missing_counts,
        missing_value_count=missing_total,
        critical_missing_value_count=critical_missing_total,
        duplicate_player_game_rows=duplicate_player_games,
        missing_label_count=missing_labels,
        invalid_label_count=invalid_labels,
        hr_positive_count=positive_count,
        hr_negative_count=negative_count,
        hr_positive_rate=_rate(positive_count, positive_count + negative_count),
        odds_available_player_games=odds_available,
        odds_coverage_rate=odds_rate,
        weather_complete_games=weather_count,
        weather_coverage_rate=weather_rate,
        ballpark_complete_games=ballpark_count,
        ballpark_coverage_rate=ballpark_rate,
        team_opponent_inconsistency_count=team_inconsistencies,
        possible_leakage_columns=leakage_columns,
        synthetic_identity_findings=synthetic_findings,
        preflight_errors=preflight.errors,
        csv_errors=tuple(csv_errors),
        blocking_reasons=tuple(blocking_reasons),
        research_review_items=research_items,
    )


def historical_backtest_readiness_to_dict(
    report: HistoricalBacktestReadinessReport,
) -> dict[str, object]:
    """Return a deterministic JSON-ready audit payload."""

    return {
        "pack_dir": str(report.pack_dir),
        "verdict": report.verdict,
        "preflight_valid": report.preflight_valid,
        "source_row_counts": dict(report.source_row_counts),
        "labeled_player_game_rows": report.labeled_player_game_rows,
        "unique_games": report.unique_games,
        "unique_players": report.unique_players,
        "unique_dates": report.unique_dates,
        "date_range_start": (
            report.date_range_start.isoformat() if report.date_range_start else None
        ),
        "date_range_end": (
            report.date_range_end.isoformat() if report.date_range_end else None
        ),
        "calendar_span_days": report.calendar_span_days,
        "missing_value_counts": {
            source_name: dict(counts)
            for source_name, counts in report.missing_value_counts.items()
        },
        "missing_value_count": report.missing_value_count,
        "critical_missing_value_count": report.critical_missing_value_count,
        "duplicate_player_game_rows": report.duplicate_player_game_rows,
        "missing_label_count": report.missing_label_count,
        "invalid_label_count": report.invalid_label_count,
        "hr_positive_count": report.hr_positive_count,
        "hr_negative_count": report.hr_negative_count,
        "hr_positive_rate": report.hr_positive_rate,
        "odds_available_player_games": report.odds_available_player_games,
        "odds_coverage_rate": report.odds_coverage_rate,
        "weather_complete_games": report.weather_complete_games,
        "weather_coverage_rate": report.weather_coverage_rate,
        "ballpark_complete_games": report.ballpark_complete_games,
        "ballpark_coverage_rate": report.ballpark_coverage_rate,
        "team_opponent_inconsistency_count": (
            report.team_opponent_inconsistency_count
        ),
        "possible_leakage_columns": list(report.possible_leakage_columns),
        "synthetic_identity_findings": list(report.synthetic_identity_findings),
        "preflight_errors": list(report.preflight_errors),
        "csv_errors": list(report.csv_errors),
        "blocking_reasons": list(report.blocking_reasons),
        "research_review_items": list(report.research_review_items),
        "approval_status": report.approval_status,
        "eligible_for_betting": report.eligible_for_betting,
        "kelly_eligible": report.kelly_eligible,
        "model_training_enabled": report.model_training_enabled,
        "backtesting_enabled": report.backtesting_enabled,
    }


def historical_backtest_readiness_to_json(
    report: HistoricalBacktestReadinessReport,
) -> str:
    """Serialize the report without writing it."""

    return json.dumps(
        historical_backtest_readiness_to_dict(report),
        indent=2,
        sort_keys=True,
    )


def historical_backtest_readiness_to_text(
    report: HistoricalBacktestReadinessReport,
) -> str:
    """Render a concise human-readable report without writing it."""

    lines = [
        "CourtVision MLB HR historical backtest readiness audit",
        "research only | read-only | local files only | not approved",
        f"pack_dir: {report.pack_dir}",
        f"verdict: {report.verdict}",
        f"preflight_valid: {str(report.preflight_valid).lower()}",
    ]
    lines.extend(
        f"{source_name}_rows: {count}"
        for source_name, count in report.source_row_counts.items()
    )
    lines.extend(
        (
            f"labeled_player_game_rows: {report.labeled_player_game_rows}",
            f"unique_games: {report.unique_games}",
            f"unique_players: {report.unique_players}",
            f"unique_dates: {report.unique_dates}",
            f"date_range_start: {report.date_range_start}",
            f"date_range_end: {report.date_range_end}",
            f"calendar_span_days: {report.calendar_span_days}",
            f"missing_value_count: {report.missing_value_count}",
            f"critical_missing_value_count: {report.critical_missing_value_count}",
            f"duplicate_player_game_rows: {report.duplicate_player_game_rows}",
            f"missing_label_count: {report.missing_label_count}",
            f"invalid_label_count: {report.invalid_label_count}",
            f"hr_positive_count: {report.hr_positive_count}",
            f"hr_negative_count: {report.hr_negative_count}",
            f"hr_positive_rate: {report.hr_positive_rate:.1%}",
            f"odds_available_player_games: {report.odds_available_player_games}",
            f"odds_coverage_rate: {report.odds_coverage_rate:.1%}",
            f"weather_complete_games: {report.weather_complete_games}",
            f"weather_coverage_rate: {report.weather_coverage_rate:.1%}",
            f"ballpark_complete_games: {report.ballpark_complete_games}",
            f"ballpark_coverage_rate: {report.ballpark_coverage_rate:.1%}",
            "team_opponent_inconsistency_count: "
            f"{report.team_opponent_inconsistency_count}",
            "possible_leakage_columns: "
            + (", ".join(report.possible_leakage_columns) or "none"),
            f"synthetic_identity_finding_count: "
            f"{len(report.synthetic_identity_findings)}",
        )
    )
    for source_name, counts in report.missing_value_counts.items():
        if counts:
            rendered = ", ".join(f"{name}={count}" for name, count in counts.items())
            lines.append(f"missing_values.{source_name}: {rendered}")
    lines.extend(f"preflight_error: {value}" for value in report.preflight_errors)
    lines.extend(f"csv_error: {value}" for value in report.csv_errors)
    lines.extend(f"blocking_reason: {value}" for value in report.blocking_reasons)
    lines.extend(
        f"research_review_item: {value}" for value in report.research_review_items
    )
    lines.extend(
        (
            f"approval_status: {report.approval_status}",
            f"eligible_for_betting: {str(report.eligible_for_betting).lower()}",
            f"kelly_eligible: {str(report.kelly_eligible).lower()}",
            f"model_training_enabled: {str(report.model_training_enabled).lower()}",
            f"backtesting_enabled: {str(report.backtesting_enabled).lower()}",
        )
    )
    return "\n".join(lines)


__all__ = [
    "MIN_RESEARCH_BALLPARK_COVERAGE_RATE",
    "MIN_RESEARCH_CALENDAR_SPAN_DAYS",
    "MIN_RESEARCH_HR_NEGATIVES",
    "MIN_RESEARCH_HR_POSITIVES",
    "MIN_RESEARCH_LABELED_PLAYER_GAMES",
    "MIN_RESEARCH_ODDS_COVERAGE_RATE",
    "MIN_RESEARCH_UNIQUE_DATES",
    "MIN_RESEARCH_UNIQUE_GAMES",
    "MIN_RESEARCH_UNIQUE_PLAYERS",
    "MIN_RESEARCH_WEATHER_COVERAGE_RATE",
    "HistoricalBacktestReadinessReport",
    "HistoricalBacktestReadinessVerdict",
    "audit_historical_backtest_readiness",
    "historical_backtest_readiness_to_dict",
    "historical_backtest_readiness_to_json",
    "historical_backtest_readiness_to_text",
]
