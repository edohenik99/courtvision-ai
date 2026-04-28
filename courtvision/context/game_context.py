from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

GAME_CONTEXT_COLUMNS: tuple[str, ...] = (
    "opponent",
    "home_away",
    "game_id",
    "postseason",
    "team_pace",
    "opponent_pace",
    "team_def_rating",
    "opponent_def_rating",
    "team_off_rating",
    "opponent_off_rating",
    "rest_days",
    "opponent_rest_days",
    "is_back_to_back",
    "opponent_is_back_to_back",
    "implied_team_total",
    "game_total",
    "spread",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>", "nat"} else text


def _float(value: Any) -> float | None:
    text = _text(value)
    if not text:
        return None
    try:
        return float(text.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _int_or_text(value: Any) -> int | str | None:
    text = _text(value)
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    return int(number) if number.is_integer() else text


def _bool_or_none(value: Any) -> bool | None:
    text = _text(value).lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _date(value: Any) -> pd.Timestamp | None:
    text = _text(value)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.tz_convert(None).normalize()


def _first(row: pd.Series | dict[str, Any] | None, names: tuple[str, ...]) -> Any:
    if row is None:
        return None
    for name in names:
        try:
            value = row.get(name)  # type: ignore[union-attr]
        except AttributeError:
            value = None
        if _text(value):
            return value
    return None


def _team_key(value: Any) -> str:
    return _text(value).upper()


def _game_lookup(games: pd.DataFrame) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    if not isinstance(games, pd.DataFrame) or games.empty:
        return lookup
    for _, row in games.iterrows():
        game_id = _text(_first(row, ("game_id", "id")))
        home_nested = row.get("home_team") if "home_team" in row.index and isinstance(row.get("home_team"), dict) else {}
        visitor_nested = row.get("visitor_team") if "visitor_team" in row.index and isinstance(row.get("visitor_team"), dict) else {}
        home = _team_key(home_nested.get("abbreviation")) or _team_key(
            _first(row, ("home_team_abbr", "home.abbreviation", "home_team.abbreviation"))
        )
        away = _team_key(visitor_nested.get("abbreviation")) or _team_key(
            _first(row, ("visitor_team_abbr", "away_team_abbr", "visitor.abbreviation", "visitor_team.abbreviation"))
        )
        postseason = _bool_or_none(_first(row, ("postseason", "is_postseason")))
        payload = {"game_id": game_id, "home": home, "away": away, "postseason": postseason}
        if game_id:
            lookup[f"id:{game_id}"] = payload
        if home and away:
            lookup[f"pair:{home}:{away}"] = payload
            lookup[f"pair:{away}:{home}"] = payload
    return lookup


def _game_teams(row: pd.Series) -> tuple[str, str]:
    home_nested = row.get("home_team") if "home_team" in row.index and isinstance(row.get("home_team"), dict) else {}
    visitor_nested = row.get("visitor_team") if "visitor_team" in row.index and isinstance(row.get("visitor_team"), dict) else {}
    home = _team_key(home_nested.get("abbreviation")) or _team_key(
        _first(row, ("home_team_abbr", "home.abbreviation", "home_team.abbreviation"))
    )
    away = _team_key(visitor_nested.get("abbreviation")) or _team_key(
        _first(row, ("visitor_team_abbr", "away_team_abbr", "visitor.abbreviation", "visitor_team.abbreviation"))
    )
    return home, away


def _rest_days_lookup(schedule_games: pd.DataFrame, prediction_date: str) -> dict[str, int]:
    slate_date = _date(prediction_date)
    if slate_date is None or not isinstance(schedule_games, pd.DataFrame) or schedule_games.empty:
        return {}
    previous_dates: dict[str, pd.Timestamp] = {}
    for _, row in schedule_games.iterrows():
        game_date = _date(_first(row, ("date", "game_date", "datetime", "status")))
        if game_date is None or game_date >= slate_date:
            continue
        home, away = _game_teams(row)
        for team in (home, away):
            if not team:
                continue
            current = previous_dates.get(team)
            if current is None or game_date > current:
                previous_dates[team] = game_date
    return {
        team: max(int((slate_date - previous_date).days) - 1, 0)
        for team, previous_date in previous_dates.items()
    }


def _team_lookup(team_baselines: pd.DataFrame) -> dict[str, pd.Series]:
    if not isinstance(team_baselines, pd.DataFrame) or team_baselines.empty or "team_abbr" not in team_baselines.columns:
        return {}
    return {
        _team_key(row.get("team_abbr")): row
        for _, row in team_baselines.iterrows()
        if _team_key(row.get("team_abbr"))
    }


def _market_lookup(odds: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    markets: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(odds, pd.DataFrame) or odds.empty:
        return markets
    for _, row in odds.iterrows():
        game_id = _text(row.get("game_id"))
        team = _team_key(_first(row, ("team", "team_abbr", "_team_abbr")))
        market_type = _text(_first(row, ("market_type", "market", "raw_market_type"))).lower()
        line = _float(_first(row, ("line", "sportsbook_line", "line_value")))
        if not game_id or line is None:
            continue
        payload = markets.setdefault((game_id, team), {})
        game_payload = markets.setdefault((game_id, ""), {})
        if "team_total" in market_type and team:
            payload["implied_team_total"] = line
        elif "spread" in market_type and team:
            payload["spread"] = line
        elif market_type in {"game_total", "total", "totals"} or "game_total" in market_type:
            game_payload["game_total"] = line
    return markets


def _candidate_game_lookup(odds: pd.DataFrame) -> dict[tuple[str, str, str], str]:
    lookup: dict[tuple[str, str, str], str] = {}
    if not isinstance(odds, pd.DataFrame) or odds.empty:
        return lookup
    for _, row in odds.iterrows():
        game_id = _text(row.get("game_id"))
        player = " ".join(_text(row.get("player_name")).lower().split())
        team = _team_key(_first(row, ("team", "team_abbr", "_team_abbr")))
        market = _text(_first(row, ("market_type", "market"))).lower()
        if not game_id or not player:
            continue
        if team and market:
            lookup.setdefault((player, team, market), game_id)
        if team:
            lookup.setdefault((player, team, ""), game_id)
        if market:
            lookup.setdefault((player, "", market), game_id)
        lookup.setdefault((player, "", ""), game_id)
    return lookup


def _team_metric(row: pd.Series | None, names: tuple[str, ...]) -> float | None:
    return _float(_first(row, names))


def _default_context() -> dict[str, Any]:
    return {
        "opponent": "",
        "home_away": "",
        "game_id": pd.NA,
        "postseason": pd.NA,
        "team_pace": pd.NA,
        "opponent_pace": pd.NA,
        "team_def_rating": pd.NA,
        "opponent_def_rating": pd.NA,
        "team_off_rating": pd.NA,
        "opponent_off_rating": pd.NA,
        "rest_days": pd.NA,
        "opponent_rest_days": pd.NA,
        "is_back_to_back": pd.NA,
        "opponent_is_back_to_back": pd.NA,
        "implied_team_total": pd.NA,
        "game_total": pd.NA,
        "spread": pd.NA,
    }


def apply_game_context(
    candidates: pd.DataFrame,
    *,
    games: pd.DataFrame,
    team_baselines: pd.DataFrame,
    odds: pd.DataFrame,
    prediction_date: str | None = None,
    schedule_games: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = candidates.copy()
    defaults = _default_context()
    for column, value in defaults.items():
        if column not in out.columns:
            out[column] = value
    diagnostics = {
        "rows": int(len(out)),
        "candidates_with_opponent": 0,
        "candidates_with_postseason": 0,
        "candidates_with_rest_days": 0,
        "candidates_with_back_to_back": 0,
        "candidates_with_def_rating": 0,
        "candidates_with_pace": 0,
        "missing_fields": [],
    }
    if out.empty:
        diagnostics["missing_fields"] = list(GAME_CONTEXT_COLUMNS)
        return out, diagnostics

    games_by_key = _game_lookup(games)
    teams = _team_lookup(team_baselines)
    markets = _market_lookup(odds)
    candidate_games = _candidate_game_lookup(odds)
    rest_lookup = _rest_days_lookup(schedule_games if schedule_games is not None else pd.DataFrame(), prediction_date or "")

    for idx, row in out.iterrows():
        team = _team_key(_first(row, ("team", "team_abbr")))
        game_id = _text(row.get("game_id"))
        if not game_id:
            player = " ".join(_text(row.get("player_name")).lower().split())
            market = _text(row.get("market_type")).lower()
            game_id = (
                candidate_games.get((player, team, market))
                or candidate_games.get((player, team, ""))
                or candidate_games.get((player, "", market))
                or candidate_games.get((player, "", ""))
                or ""
            )
            if game_id:
                out.at[idx, "game_id"] = _int_or_text(game_id)
        game = games_by_key.get(f"id:{game_id}") if game_id else None
        if game is None and team:
            for payload in games_by_key.values():
                if payload.get("home") == team or payload.get("away") == team:
                    game = payload
                    break
        if game:
            home = _team_key(game.get("home"))
            away = _team_key(game.get("away"))
            opponent = away if team == home else home if team == away else ""
            out.at[idx, "opponent"] = opponent
            out.at[idx, "home_away"] = "home" if team == home else "away" if team == away else ""
            if game.get("game_id"):
                out.at[idx, "game_id"] = _int_or_text(game.get("game_id"))
            if game.get("postseason") is not None:
                out.at[idx, "postseason"] = bool(game.get("postseason"))

        opponent = _team_key(out.at[idx, "opponent"])
        team_row = teams.get(team)
        opp_row = teams.get(opponent)
        out.at[idx, "team_pace"] = _team_metric(team_row, ("team_pace", "pace", "possessions_per_game"))
        out.at[idx, "opponent_pace"] = _team_metric(opp_row, ("team_pace", "pace", "possessions_per_game"))
        out.at[idx, "team_def_rating"] = _team_metric(team_row, ("team_def_rating", "def_rating", "defensive_rating"))
        out.at[idx, "opponent_def_rating"] = _team_metric(opp_row, ("team_def_rating", "def_rating", "defensive_rating"))
        out.at[idx, "team_off_rating"] = _team_metric(team_row, ("team_off_rating", "off_rating", "offensive_rating"))
        out.at[idx, "opponent_off_rating"] = _team_metric(opp_row, ("team_off_rating", "off_rating", "offensive_rating"))
        team_rest = rest_lookup.get(team)
        opponent_rest = rest_lookup.get(opponent)
        out.at[idx, "rest_days"] = float(team_rest) if team_rest is not None else _team_metric(team_row, ("rest_days", "team_rest_days"))
        out.at[idx, "opponent_rest_days"] = (
            float(opponent_rest) if opponent_rest is not None else _team_metric(opp_row, ("rest_days", "team_rest_days"))
        )
        rest_days = _float(out.at[idx, "rest_days"])
        opp_rest_days = _float(out.at[idx, "opponent_rest_days"])
        out.at[idx, "is_back_to_back"] = bool(rest_days == 0.0) if rest_days is not None else pd.NA
        out.at[idx, "opponent_is_back_to_back"] = bool(opp_rest_days == 0.0) if opp_rest_days is not None else pd.NA

        market_payload = markets.get((_text(out.at[idx, "game_id"]), team), {})
        game_payload = markets.get((_text(out.at[idx, "game_id"]), ""), {})
        for column in ("implied_team_total", "spread"):
            if column in market_payload:
                out.at[idx, column] = market_payload[column]
        if "game_total" in game_payload:
            out.at[idx, "game_total"] = game_payload["game_total"]

    diagnostics.update(_coverage(out))
    return out, diagnostics


def _coverage(df: pd.DataFrame) -> dict[str, Any]:
    def has_text(column: str) -> int:
        return int(df[column].map(lambda value: bool(_text(value))).sum()) if column in df.columns else 0

    def has_value(column: str) -> int:
        return int(df[column].map(lambda value: _text(value) != "").sum()) if column in df.columns else 0

    def has_any(columns: tuple[str, ...]) -> int:
        if not all(column in df.columns for column in columns):
            return 0
        mask = pd.Series(False, index=df.index)
        for column in columns:
            mask = mask | df[column].map(lambda value: _text(value) != "")
        return int(mask.sum())

    def has_true(columns: tuple[str, ...]) -> int:
        if not all(column in df.columns for column in columns):
            return 0
        mask = pd.Series(False, index=df.index)
        for column in columns:
            mask = mask | df[column].map(lambda value: _text(value).lower() == "true")
        return int(mask.sum())

    coverage = {
        "rows": int(len(df)),
        "candidates_with_opponent": has_text("opponent"),
        "candidates_with_postseason": has_value("postseason"),
        "candidates_with_rest_days": has_any(("rest_days", "opponent_rest_days")),
        "candidates_with_back_to_back": has_true(("is_back_to_back", "opponent_is_back_to_back")),
        "candidates_with_def_rating": has_any(("team_def_rating", "opponent_def_rating")),
        "candidates_with_pace": has_any(("team_pace", "opponent_pace")),
    }
    coverage["missing_fields"] = [
        column for column in GAME_CONTEXT_COLUMNS if column in df.columns and has_value(column) == 0
    ]
    return coverage


def write_game_context_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path,
    diagnostics: dict[str, Any],
    candidates: pd.DataFrame,
) -> tuple[Path, Path, dict[str, Any]]:
    payload = {
        "prediction_date": prediction_date,
        **diagnostics,
        "passive_mode": True,
        "projection_changed": False,
        "confidence_changed": False,
        "selection_logic_changed": False,
        "kelly_logic_changed": False,
        "sample_rows": candidates[[c for c in ["player_name", "team", *GAME_CONTEXT_COLUMNS] if c in candidates.columns]]
        .head(20)
        .to_dict("records")
        if not candidates.empty
        else [],
    }
    runtime_root = Path(runtime_root)
    diagnostics_dir = runtime_root / "diagnostics"
    operator_dir = runtime_root / "operator"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    operator_dir.mkdir(parents=True, exist_ok=True)
    json_path = diagnostics_dir / f"game_context_{prediction_date}.json"
    txt_path = operator_dir / f"game_context_report_{prediction_date}.txt"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    lines = [
        f"Game Context Diagnostics - {prediction_date}",
        "",
        f"rows: {payload['rows']}",
        f"candidates_with_opponent: {payload['candidates_with_opponent']}",
        f"candidates_with_postseason: {payload['candidates_with_postseason']}",
        f"candidates_with_rest_days: {payload['candidates_with_rest_days']}",
        f"candidates_with_back_to_back: {payload.get('candidates_with_back_to_back', 0)}",
        f"candidates_with_def_rating: {payload['candidates_with_def_rating']}",
        f"candidates_with_pace: {payload['candidates_with_pace']}",
        f"missing_fields: {payload['missing_fields']}",
        "",
        "Mode: passive diagnostics only. No projections, confidence, selection, elite, or Kelly logic changed.",
    ]
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, txt_path, payload
