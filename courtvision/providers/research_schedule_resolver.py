"""Research-only schedule resolver for API-NBA and manual schedule fallback.

This module is deliberately decoupled from odds, betting, Kelly, Elite, and
runtime scoring paths. It resolves schedule rows only and never creates market
or wager rows.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

DEFAULT_MANUAL_SCHEDULE_DIR = Path("data/manual_schedule")
DEFAULT_RUNTIME_ROOT = Path("outputs/runtime")
PROVIDER_STATUS_OK = "ok"
PROVIDER_STATUS_MISSING = "research_schedule_missing"
PROVIDER_STATUS_INVALID = "research_schedule_invalid"

SOURCE_API_NBA = "api_nba"
SOURCE_MANUAL_SCHEDULE = "manual_schedule"
SOURCE_NONE = "none"
RESEARCH_MODE = "research"

REQUIRED_MANUAL_COLUMNS = (
    "game_date",
    "game_id",
    "home_team",
    "away_team",
    "home_team_abbr",
    "away_team_abbr",
    "source",
)
SCHEDULE_COLUMNS = REQUIRED_MANUAL_COLUMNS + ("mode", "eligible_for_betting")


@dataclass(slots=True)
class ResearchScheduleResult:
    schedule: pd.DataFrame
    provider_status: str
    selected_source: str
    diagnostics: dict[str, Any]
    diagnostics_path: Path | None = None
    market_props: list[Any] = field(default_factory=list)


def manual_schedule_path_for_date(
    target_date: str,
    manual_schedule_dir: str | Path = DEFAULT_MANUAL_SCHEDULE_DIR,
) -> Path:
    return Path(manual_schedule_dir) / f"manual_games_{target_date}.csv"


def diagnostics_path_for_date(
    target_date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"research_schedule_resolver_{target_date}.json"


def resolve_research_schedule(
    target_date: str,
    api_nba_games_result: Any | None = None,
    *,
    manual_schedule_dir: str | Path = DEFAULT_MANUAL_SCHEDULE_DIR,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    diagnostics_path: str | Path | None = None,
    write_diagnostics: bool = True,
) -> ResearchScheduleResult:
    """Resolve a research-only schedule without creating betting artifacts."""
    target_date_text = str(target_date)
    warnings: list[str] = []
    manual_path = manual_schedule_path_for_date(target_date_text, manual_schedule_dir)
    manual_schedule_available = manual_path.exists()

    api_schedule, api_warnings = _normalize_api_nba_games(api_nba_games_result, target_date_text)
    warnings.extend(api_warnings)
    api_nba_games_available = not api_schedule.empty

    selected_source = SOURCE_NONE
    provider_status = PROVIDER_STATUS_MISSING
    schedule = _empty_schedule()

    if api_nba_games_available:
        selected_source = SOURCE_API_NBA
        provider_status = PROVIDER_STATUS_OK
        schedule = api_schedule
    elif manual_schedule_available:
        manual_schedule, manual_warnings = load_manual_schedule(
            target_date_text,
            manual_schedule_dir=manual_schedule_dir,
        )
        warnings.extend(manual_warnings)
        selected_source = SOURCE_MANUAL_SCHEDULE
        schedule = manual_schedule
        provider_status = PROVIDER_STATUS_OK if not schedule.empty else PROVIDER_STATUS_INVALID
    else:
        warnings.append(f"manual_schedule_file_missing: {manual_path}")

    diagnostics = {
        "target_date": target_date_text,
        "api_nba_games_available": bool(api_nba_games_available),
        "manual_schedule_available": bool(manual_schedule_available),
        "selected_source": selected_source,
        "game_count": int(len(schedule.index)),
        "provider_status": provider_status,
        "warnings": warnings,
    }

    resolved_diagnostics_path = (
        Path(diagnostics_path)
        if diagnostics_path is not None
        else diagnostics_path_for_date(target_date_text, runtime_root)
    )
    if write_diagnostics:
        _write_diagnostics(resolved_diagnostics_path, diagnostics)

    return ResearchScheduleResult(
        schedule=schedule,
        provider_status=provider_status,
        selected_source=selected_source,
        diagnostics=diagnostics,
        diagnostics_path=resolved_diagnostics_path if write_diagnostics else None,
        market_props=[],
    )


def load_manual_schedule(
    target_date: str,
    *,
    manual_schedule_dir: str | Path = DEFAULT_MANUAL_SCHEDULE_DIR,
) -> tuple[pd.DataFrame, list[str]]:
    """Read and validate the dated manual schedule CSV."""
    target_date_text = str(target_date)
    path = manual_schedule_path_for_date(target_date_text, manual_schedule_dir)
    warnings: list[str] = []

    if not path.exists():
        warnings.append(f"manual_schedule_file_missing: {path}")
        return _empty_schedule(), warnings

    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception as exc:
        warnings.append(f"manual_schedule_read_failed: {exc}")
        return _empty_schedule(), warnings

    missing_columns = [column for column in REQUIRED_MANUAL_COLUMNS if column not in raw.columns]
    if missing_columns:
        warnings.append(f"manual_schedule_missing_columns: {', '.join(missing_columns)}")
        return _empty_schedule(), warnings

    valid_rows: list[dict[str, Any]] = []
    seen_game_ids: set[str] = set()
    for index, row in raw.iterrows():
        normalized, row_errors = _validate_manual_row(row, target_date_text)
        game_id = str(normalized.get("game_id") or "").strip()
        if game_id in seen_game_ids:
            row_errors.append("duplicate_game_id")
        if row_errors:
            csv_line = int(index) + 2
            warnings.append(
                f"manual_schedule_row_rejected line={csv_line}: {', '.join(row_errors)}"
            )
            continue
        seen_game_ids.add(game_id)
        valid_rows.append(normalized)

    return _schedule_from_rows(valid_rows), warnings


def _normalize_api_nba_games(value: Any | None, target_date: str) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    if value is None:
        return _empty_schedule(), warnings
    if isinstance(value, str) and not value.strip():
        return _empty_schedule(), warnings
    if isinstance(value, pd.DataFrame) and value.empty:
        return _empty_schedule(), warnings
    if isinstance(value, (list, tuple, dict)) and not value:
        return _empty_schedule(), warnings

    raw_items = list(_iter_schedule_items(value))
    valid_rows: list[dict[str, Any]] = []
    for item in raw_items:
        row = _api_nba_row(item, target_date)
        if row is not None:
            valid_rows.append(row)

    if raw_items and not valid_rows:
        warnings.append("api_nba_games_result_no_target_date_rows")

    return _schedule_from_rows(valid_rows), warnings


def _iter_schedule_items(value: Any) -> Iterable[Any]:
    if isinstance(value, pd.DataFrame):
        yield from value.to_dict("records")
        return

    if isinstance(value, dict):
        response = value.get("response")
        if isinstance(response, list):
            yield from response
            return
        if isinstance(response, dict):
            yield response
            return
        yield value
        return

    if isinstance(value, (list, tuple)):
        yield from value
        return

    yield value


def _api_nba_row(item: Any, target_date: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return _api_nba_object_row(item, target_date)

    game_date = _first_text(
        _path_value(item, "game_date"),
        _path_value(item, "date.start"),
        _path_value(item, "game.date"),
        _path_value(item, "date"),
        _path_value(item, "start"),
    )[:10]
    if game_date != target_date:
        return None

    game_id = _first_text(_path_value(item, "game_id"), _path_value(item, "game.id"), item.get("id"))
    home_team = _first_text(
        _path_value(item, "teams.home.name"),
        _path_value(item, "home.name"),
        _path_value(item, "home_team.full_name"),
        _path_value(item, "home_team"),
    )
    away_team = _first_text(
        _path_value(item, "teams.visitors.name"),
        _path_value(item, "teams.away.name"),
        _path_value(item, "visitors.name"),
        _path_value(item, "away.name"),
        _path_value(item, "visitor_team.full_name"),
        _path_value(item, "away_team"),
    )
    if not game_id or not home_team or not away_team:
        return None

    return _research_schedule_row(
        game_date=game_date,
        game_id=game_id,
        home_team=home_team,
        away_team=away_team,
        home_team_abbr=_first_text(
            _path_value(item, "home_team_abbr"),
            _path_value(item, "teams.home.code"),
            _path_value(item, "teams.home.abbreviation"),
            _path_value(item, "home.abbreviation"),
        ).upper(),
        away_team_abbr=_first_text(
            _path_value(item, "away_team_abbr"),
            _path_value(item, "teams.visitors.code"),
            _path_value(item, "teams.visitors.abbreviation"),
            _path_value(item, "teams.away.code"),
            _path_value(item, "away.abbreviation"),
        ).upper(),
        source=SOURCE_API_NBA,
    )


def _api_nba_object_row(item: Any, target_date: str) -> dict[str, Any] | None:
    game_date = _first_text(getattr(item, "game_date", ""), getattr(item, "date", ""))[:10]
    if game_date != target_date:
        return None

    home_team = getattr(item, "home_team", None)
    away_team = getattr(item, "visitor_team", None) or getattr(item, "away_team", None)
    game_id = _first_text(getattr(item, "game_id", ""), getattr(item, "id", ""))
    home_name = _first_text(getattr(home_team, "full_name", ""), getattr(home_team, "name", ""))
    away_name = _first_text(getattr(away_team, "full_name", ""), getattr(away_team, "name", ""))
    if not game_id or not home_name or not away_name:
        return None

    return _research_schedule_row(
        game_date=game_date,
        game_id=game_id,
        home_team=home_name,
        away_team=away_name,
        home_team_abbr=_first_text(getattr(home_team, "abbreviation", "")).upper(),
        away_team_abbr=_first_text(getattr(away_team, "abbreviation", "")).upper(),
        source=SOURCE_API_NBA,
    )


def _validate_manual_row(row: pd.Series, target_date: str) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    values = {column: _clean_text(row.get(column, "")) for column in REQUIRED_MANUAL_COLUMNS}

    for column, value in values.items():
        if not value:
            errors.append(f"missing_{column}")

    if values["game_date"] and values["game_date"] != target_date:
        errors.append("wrong_game_date")

    if values["source"] and values["source"] != SOURCE_MANUAL_SCHEDULE:
        errors.append("invalid_source")

    normalized = _research_schedule_row(
        game_date=values["game_date"],
        game_id=values["game_id"],
        home_team=values["home_team"],
        away_team=values["away_team"],
        home_team_abbr=values["home_team_abbr"].upper(),
        away_team_abbr=values["away_team_abbr"].upper(),
        source=SOURCE_MANUAL_SCHEDULE,
    )
    return normalized, errors


def _research_schedule_row(
    *,
    game_date: str,
    game_id: Any,
    home_team: str,
    away_team: str,
    home_team_abbr: str,
    away_team_abbr: str,
    source: str,
) -> dict[str, Any]:
    return {
        "game_date": _clean_text(game_date),
        "game_id": _clean_text(game_id),
        "home_team": _clean_text(home_team),
        "away_team": _clean_text(away_team),
        "home_team_abbr": _clean_text(home_team_abbr),
        "away_team_abbr": _clean_text(away_team_abbr),
        "source": _clean_text(source),
        "mode": RESEARCH_MODE,
        "eligible_for_betting": False,
    }


def _schedule_from_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return _empty_schedule()
    return pd.DataFrame(rows, columns=SCHEDULE_COLUMNS)


def _empty_schedule() -> pd.DataFrame:
    return pd.DataFrame(columns=SCHEDULE_COLUMNS)


def _write_diagnostics(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:
        payload.setdefault("warnings", []).append(f"diagnostics_write_failed: {exc}")


def _path_value(item: dict[str, Any], path: str) -> Any:
    current: Any = item
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


__all__ = [
    "DEFAULT_MANUAL_SCHEDULE_DIR",
    "DEFAULT_RUNTIME_ROOT",
    "PROVIDER_STATUS_INVALID",
    "PROVIDER_STATUS_MISSING",
    "PROVIDER_STATUS_OK",
    "REQUIRED_MANUAL_COLUMNS",
    "RESEARCH_MODE",
    "ResearchScheduleResult",
    "SCHEDULE_COLUMNS",
    "SOURCE_API_NBA",
    "SOURCE_MANUAL_SCHEDULE",
    "SOURCE_NONE",
    "diagnostics_path_for_date",
    "load_manual_schedule",
    "manual_schedule_path_for_date",
    "resolve_research_schedule",
]
