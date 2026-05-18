from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Mapping
from typing import Any

import pandas as pd

from courtvision.artifact_guard import log_prediction_artifact_write

GAME_CONTEXT_COLUMNS: tuple[str, ...] = (
    "game_id",
    "opponent",
    "home_away",
    "postseason",
    "rest_days",
    "opponent_rest_days",
    "rest_edge",
    "is_back_to_back",
    "opponent_is_back_to_back",
    "team_pace",
    "opponent_pace",
    "matchup_pace",
    "team_off_rating",
    "opponent_off_rating",
    "team_def_rating",
    "opponent_def_rating",
    "team_net_rating",
    "opponent_net_rating",
    "implied_team_total",
    "opponent_implied_team_total",
    "game_total",
    "spread",
    "pace_context_signal",
    "defense_context_signal",
    "rest_context_signal",
    "playoff_context_signal",
    "overall_context_signal",
    "context_pick_alignment",
    "context_caution_level",
    "context_preview_applied",
)

GAME_CONTEXT_DIAGNOSTIC_COLUMNS: tuple[str, ...] = (
    "game_home_team_abbr",
    "game_away_team_abbr",
    "candidate_team_not_in_game",
    "game_context_suppressed",
    "game_context_suppression_reason",
    "playoff_only_high_caution",
    "context_conflict_cause",
    "identity_team_conflict",
    "identity_team_conflict_reason",
    "identity_quarantine_reason",
)

CONTEXT_CONFLICT_CAUSE_BUCKETS: tuple[str, ...] = (
    "playoff_only",
    "defense_driven",
    "pace_driven",
    "pace_defense_combined",
    "playoff_defense_combined",
    "stale_team_not_in_game",
)

IDENTITY_QUARANTINE_REJECTION_REASON = "identity_quarantine"
IDENTITY_QUARANTINE_ACTION = "DATA_INVALID"
IDENTITY_OUTSIDE_TEAM_REASON = "outside_team_identity"
IDENTITY_STALE_TEAM_REASON = "stale_team_identity"
IDENTITY_GAME_NOT_BETTABLE_REASON = "game_not_bettable"
IDENTITY_QUARANTINE_REASONS: tuple[str, ...] = (
    IDENTITY_OUTSIDE_TEAM_REASON,
    IDENTITY_STALE_TEAM_REASON,
    IDENTITY_GAME_NOT_BETTABLE_REASON,
)
IDENTITY_SOURCE_TEAM_COLUMNS: tuple[str, ...] = (
    "provider_team_abbr",
    "odds_team_abbr",
    "baseline_team_abbr",
    "resolved_team_abbr",
    "identity_source_team_abbr",
)

PACE_COLUMNS: tuple[str, ...] = ("team_pace", "pace", "possessions_per_game")
OFF_RATING_COLUMNS: tuple[str, ...] = ("team_off_rating", "off_rating", "offensive_rating")
DEF_RATING_COLUMNS: tuple[str, ...] = ("team_def_rating", "def_rating", "defensive_rating")
NET_RATING_COLUMNS: tuple[str, ...] = ("team_net_rating", "net_rating")


def _nullable(value: Any) -> Any:
    return value if value is not None else pd.NA


def _average(left: Any, right: Any) -> float | None:
    left_value = _float(left)
    right_value = _float(right)
    if left_value is None or right_value is None:
        return None
    return (left_value + right_value) / 2.0


def _net_rating(row: pd.Series | None) -> float | None:
    explicit = _team_metric(row, NET_RATING_COLUMNS)
    if explicit is not None:
        return explicit
    off_rating = _team_metric(row, OFF_RATING_COLUMNS)
    def_rating = _team_metric(row, DEF_RATING_COLUMNS)
    if off_rating is None or def_rating is None:
        return None
    return off_rating - def_rating


def _pace_signal(team_pace: Any, opponent_pace: Any, matchup_pace: Any) -> str:
    pace = _float(matchup_pace)
    if pace is None:
        pace = _average(team_pace, opponent_pace)
    if pace is None:
        pace = _float(team_pace) or _float(opponent_pace)
    if pace is None:
        return "insufficient_data"
    if pace >= 100.0:
        return "supports_over"
    if pace <= 97.0:
        return "supports_under"
    return "neutral"


def _defense_signal(opponent_def_rating: Any, opponent_net_rating: Any) -> str:
    defense = _float(opponent_def_rating)
    net = _float(opponent_net_rating)
    if defense is None and net is None:
        return "insufficient_data"
    if (defense is not None and defense <= 112.0) or (net is not None and net >= 5.0):
        return "supports_under"
    if (defense is not None and defense >= 116.0) or (net is not None and net <= -5.0):
        return "supports_over"
    return "neutral"


def _rest_signal(rest_days: Any, opponent_rest_days: Any, is_back_to_back: Any, opponent_is_back_to_back: Any) -> str:
    rest = _float(rest_days)
    opponent_rest = _float(opponent_rest_days)
    team_b2b = _bool_or_none(is_back_to_back)
    opponent_b2b = _bool_or_none(opponent_is_back_to_back)
    if rest is None and opponent_rest is None and team_b2b is None and opponent_b2b is None:
        return "insufficient_data"
    if team_b2b is True or rest == 0.0:
        return "supports_under"
    if opponent_b2b is True and team_b2b is not True:
        return "supports_over"
    if rest is not None and opponent_rest is not None:
        edge = rest - opponent_rest
        if edge >= 2.0:
            return "supports_over"
        if edge <= -2.0:
            return "supports_under"
    return "neutral"


def _playoff_signal(postseason: Any) -> str:
    flag = _bool_or_none(postseason)
    if flag is None:
        return "insufficient_data"
    return "supports_under" if flag else "neutral"


def _overall_signal(signals: tuple[str, ...]) -> str:
    useful = [signal for signal in signals if signal != "insufficient_data"]
    if not useful:
        return "insufficient_data"
    over_count = useful.count("supports_over")
    under_count = useful.count("supports_under")
    if over_count > under_count:
        return "supports_over"
    if under_count > over_count:
        return "supports_under"
    return "neutral"


def _context_pick_alignment(selection: Any, overall_context_signal: Any) -> str:
    side = _text(selection).lower()
    signal = _text(overall_context_signal).lower()
    if signal in {"", "insufficient_data"}:
        return "insufficient_data"
    if signal == "neutral":
        return "neutral"
    if side == "over":
        return "aligned" if signal == "supports_over" else "conflicted" if signal == "supports_under" else "insufficient_data"
    if side == "under":
        return "aligned" if signal == "supports_under" else "conflicted" if signal == "supports_over" else "insufficient_data"
    return "insufficient_data"


def _context_caution_level(selection: Any, overall_context_signal: Any, context_pick_alignment: Any) -> str:
    side = _text(selection).lower()
    signal = _text(overall_context_signal).lower()
    alignment = _text(context_pick_alignment).lower()
    if alignment == "conflicted" and signal == "supports_under" and side == "over":
        return "high"
    if alignment == "neutral":
        return "medium"
    if alignment == "aligned":
        return "low"
    return "insufficient_data"


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


def _row_get(row: Mapping[str, Any] | pd.Series, key: str, default: Any = None) -> Any:
    if isinstance(row, pd.Series):
        return row.get(key, default)
    return row.get(key, default)


def _truthy(value: Any) -> bool:
    return _text(value).lower() in {"true", "1", "yes", "y", "on"}


def _team_abbr(value: Any) -> str:
    return _text(value).upper()


def _first_team(row: Mapping[str, Any] | pd.Series, columns: tuple[str, ...]) -> str:
    for column in columns:
        value = _team_abbr(_row_get(row, column))
        if value:
            return value
    return ""


def _candidate_team(row: Mapping[str, Any] | pd.Series) -> str:
    return _first_team(row, ("team_abbr", "team", "team_abbreviation"))


def _identity_game_teams(row: Mapping[str, Any] | pd.Series) -> tuple[str, str]:
    home = _first_team(
        row,
        (
            "game_home_team_abbr",
            "home_team_abbr",
            "home_team",
            "home",
        ),
    )
    away = _first_team(
        row,
        (
            "game_away_team_abbr",
            "game_visitor_team_abbr",
            "visitor_team_abbr",
            "away_team_abbr",
            "away_team",
            "visitor_team",
            "away",
        ),
    )
    return home, away


def _has_game_identity_context(row: Mapping[str, Any] | pd.Series, home: str, away: str) -> bool:
    return bool(_text(_row_get(row, "game_id")) or (home and away))


def is_identity_quarantined(row: Mapping[str, Any] | pd.Series) -> str | None:
    """Return a narrow identity-quarantine reason for explicit stale/wrong-team evidence."""
    explicit_reason = _text(_row_get(row, "identity_quarantine_reason")).lower()
    if explicit_reason in IDENTITY_QUARANTINE_REASONS:
        return explicit_reason
    if _text(_row_get(row, "selection_rejection_reason")).lower() == IDENTITY_QUARANTINE_REJECTION_REASON:
        return explicit_reason or IDENTITY_GAME_NOT_BETTABLE_REASON
    if _truthy(_row_get(row, "candidate_team_not_in_game")):
        return IDENTITY_OUTSIDE_TEAM_REASON
    if _text(_row_get(row, "context_conflict_cause")).lower() == "stale_team_not_in_game":
        return IDENTITY_OUTSIDE_TEAM_REASON

    candidate = _candidate_team(row)
    home, away = _identity_game_teams(row)
    game_teams = {team for team in (home, away) if team}
    if candidate and game_teams and _has_game_identity_context(row, home, away) and candidate not in game_teams:
        return IDENTITY_OUTSIDE_TEAM_REASON

    if candidate and candidate in game_teams:
        for column in IDENTITY_SOURCE_TEAM_COLUMNS:
            source_team = _team_abbr(_row_get(row, column))
            if source_team and source_team != candidate:
                return IDENTITY_STALE_TEAM_REASON

    return None


def identity_quarantine_reason_counts(df: pd.DataFrame) -> dict[str, int]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    counts: dict[str, int] = {}
    for _, row in df.iterrows():
        reason = is_identity_quarantined(row)
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _safe_count(value: Any, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def identity_quarantine_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    empty = {
        "rejection_reason": IDENTITY_QUARANTINE_REJECTION_REASON,
        "total_rows_dropped": 0,
        "counts_by_reason": {},
    }
    if not isinstance(payload, Mapping):
        return empty

    existing = payload.get("identity_quarantine")
    if isinstance(existing, Mapping):
        counts = existing.get("counts_by_reason") or existing.get("identity_quarantine_reason_counts") or {}
        counts = {str(key): _safe_count(value) for key, value in counts.items()} if isinstance(counts, Mapping) else {}
        total = _safe_count(
            existing.get("total_rows_dropped")
            or existing.get("identity_quarantine_count")
            or sum(counts.values())
            or 0
        )
        return {
            "rejection_reason": str(existing.get("rejection_reason") or IDENTITY_QUARANTINE_REJECTION_REASON),
            "total_rows_dropped": total,
            "counts_by_reason": dict(sorted((key, value) for key, value in counts.items() if value > 0)),
        }

    for nested_key in ("final_board_construction", "candidate_funnel", "summary"):
        nested = payload.get(nested_key)
        if isinstance(nested, Mapping):
            summary = identity_quarantine_summary(nested)
            if summary["total_rows_dropped"] > 0 or summary["counts_by_reason"]:
                return summary

    counts: dict[str, int] = {}
    for key in ("identity_quarantine_reason_counts", "counts_by_reason"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            counts = {
                str(reason): _safe_count(count)
                for reason, count in value.items()
                if _safe_count(count) > 0
            }
            break

    total = _safe_count(payload.get("identity_quarantine_count") or payload.get("total_rows_dropped") or sum(counts.values()) or 0)
    if total > 0 or counts:
        return {
            "rejection_reason": str(payload.get("identity_quarantine_rejection_reason") or IDENTITY_QUARANTINE_REJECTION_REASON),
            "total_rows_dropped": total,
            "counts_by_reason": dict(sorted(counts.items())),
        }

    for scope in ("full_market", "elite"):
        scope_payload = payload.get(scope)
        if not isinstance(scope_payload, Mapping):
            continue
        summary = identity_quarantine_summary(scope_payload)
        if summary["total_rows_dropped"] > 0 or summary["counts_by_reason"]:
            return summary

    return empty


def format_identity_quarantine_line(payload: Mapping[str, Any] | None) -> str:
    summary = identity_quarantine_summary(payload)
    total = int(summary.get("total_rows_dropped", 0) or 0)
    if total <= 0:
        return ""
    counts = summary.get("counts_by_reason", {})
    if isinstance(counts, Mapping) and counts:
        count_text = ", ".join(f"{reason}={count}" for reason, count in sorted(counts.items()))
        return f"identity quarantined: {total} ({count_text})"
    return f"identity quarantined: {total}"


def _mark_identity_quarantine_row(row: Mapping[str, Any] | pd.Series) -> dict[str, Any]:
    out = dict(row)
    reason = is_identity_quarantined(out)
    if not reason:
        if "identity_team_conflict" not in out or not _text(out.get("identity_team_conflict")):
            out["identity_team_conflict"] = False
        if "identity_team_conflict_reason" not in out or not _text(out.get("identity_team_conflict_reason")):
            out["identity_team_conflict_reason"] = ""
        if "identity_quarantine_reason" not in out or not _text(out.get("identity_quarantine_reason")):
            out["identity_quarantine_reason"] = ""
        if not _text(out.get("selection_rejection_reason")):
            out["selection_rejection_reason"] = ""
        if not _text(out.get("rejection_reason")):
            out["rejection_reason"] = ""
        return out

    out["identity_team_conflict"] = True
    out["identity_team_conflict_reason"] = reason
    out["identity_quarantine_reason"] = reason
    out["recommended_action"] = IDENTITY_QUARANTINE_ACTION
    out["selection_rejection_reason"] = IDENTITY_QUARANTINE_REJECTION_REASON
    out["rejection_reason"] = IDENTITY_QUARANTINE_REJECTION_REASON
    return out


def mark_identity_quarantine_fields(data: pd.DataFrame | Mapping[str, Any] | pd.Series) -> pd.DataFrame | dict[str, Any]:
    if isinstance(data, pd.DataFrame):
        if data.empty:
            out = data.copy()
            for column, default in (
                ("identity_team_conflict", False),
                ("identity_team_conflict_reason", ""),
                ("identity_quarantine_reason", ""),
            ):
                if column not in out.columns:
                    out[column] = default
            return out
        return pd.DataFrame([_mark_identity_quarantine_row(row) for _, row in data.iterrows()], index=data.index)
    return _mark_identity_quarantine_row(data)


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
        "game_id": pd.NA,
        "opponent": "",
        "home_away": "",
        "postseason": pd.NA,
        "rest_days": pd.NA,
        "opponent_rest_days": pd.NA,
        "rest_edge": pd.NA,
        "is_back_to_back": pd.NA,
        "opponent_is_back_to_back": pd.NA,
        "team_pace": pd.NA,
        "opponent_pace": pd.NA,
        "matchup_pace": pd.NA,
        "team_off_rating": pd.NA,
        "opponent_off_rating": pd.NA,
        "team_def_rating": pd.NA,
        "opponent_def_rating": pd.NA,
        "team_net_rating": pd.NA,
        "opponent_net_rating": pd.NA,
        "implied_team_total": pd.NA,
        "opponent_implied_team_total": pd.NA,
        "game_total": pd.NA,
        "spread": pd.NA,
        "pace_context_signal": "insufficient_data",
        "defense_context_signal": "insufficient_data",
        "rest_context_signal": "insufficient_data",
        "playoff_context_signal": "insufficient_data",
        "overall_context_signal": "insufficient_data",
        "context_pick_alignment": "insufficient_data",
        "context_caution_level": "insufficient_data",
        "context_preview_applied": False,
        "game_home_team_abbr": "",
        "game_away_team_abbr": "",
        "candidate_team_not_in_game": False,
        "game_context_suppressed": False,
        "game_context_suppression_reason": "",
        "playoff_only_high_caution": False,
        "context_conflict_cause": "",
        "identity_team_conflict": False,
        "identity_team_conflict_reason": "",
        "identity_quarantine_reason": "",
    }


def _clear_applied_game_context(out: pd.DataFrame, idx: Any) -> None:
    defaults = _default_context()
    for column in GAME_CONTEXT_COLUMNS:
        if column == "game_id":
            continue
        out.at[idx, column] = defaults[column]


def _playoff_only_high_caution(row: pd.Series) -> bool:
    if _text(row.get("selection")).lower() != "over":
        return False
    if _text(row.get("context_caution_level")).lower() != "high":
        return False
    if _text(row.get("context_pick_alignment")).lower() != "conflicted":
        return False
    if _text(row.get("overall_context_signal")).lower() != "supports_under":
        return False
    if _text(row.get("playoff_context_signal")).lower() != "supports_under":
        return False
    other_signals = (
        _text(row.get("pace_context_signal")).lower(),
        _text(row.get("defense_context_signal")).lower(),
        _text(row.get("rest_context_signal")).lower(),
    )
    return all(signal != "supports_under" for signal in other_signals)


def _context_conflict_cause(row: pd.Series) -> str:
    if _text(row.get("candidate_team_not_in_game")).lower() == "true":
        return "stale_team_not_in_game"
    if _text(row.get("game_context_suppression_reason")).lower() == "team_not_in_game_context":
        return "stale_team_not_in_game"
    if _text(row.get("selection")).lower() != "over":
        return ""
    if _text(row.get("context_caution_level")).lower() != "high":
        return ""
    if _text(row.get("context_pick_alignment")).lower() != "conflicted":
        return ""

    pace_under = _text(row.get("pace_context_signal")).lower() == "supports_under"
    defense_under = _text(row.get("defense_context_signal")).lower() == "supports_under"
    rest_under = _text(row.get("rest_context_signal")).lower() == "supports_under"
    playoff_under = _text(row.get("playoff_context_signal")).lower() == "supports_under"

    if pace_under and defense_under:
        return "pace_defense_combined"
    if playoff_under and defense_under:
        return "playoff_defense_combined"
    if playoff_under and not any((pace_under, defense_under, rest_under)):
        return "playoff_only"
    if defense_under:
        return "defense_driven"
    if pace_under:
        return "pace_driven"
    return ""


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
    for column in (*GAME_CONTEXT_COLUMNS, *GAME_CONTEXT_DIAGNOSTIC_COLUMNS):
        if column in out.columns:
            out[column] = out[column].astype("object")
    diagnostics = {
        "rows": int(len(out)),
        "total_candidates": int(len(out)),
        "candidates_with_game_id": 0,
        "candidates_with_opponent": 0,
        "candidates_with_home_away": 0,
        "candidates_with_postseason": 0,
        "candidates_with_rest_days": 0,
        "candidates_with_back_to_back": 0,
        "candidates_with_def_rating": 0,
        "candidates_with_off_rating": 0,
        "candidates_with_pace": 0,
        "missing_fields": [],
        "missing_fields_breakdown": {},
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
            out.at[idx, "game_home_team_abbr"] = home
            out.at[idx, "game_away_team_abbr"] = away
            team_in_game = bool(team and team in {home, away})
            if team and home and away and not team_in_game:
                _clear_applied_game_context(out, idx)
                out.at[idx, "candidate_team_not_in_game"] = True
                out.at[idx, "game_context_suppressed"] = True
                out.at[idx, "game_context_suppression_reason"] = "team_not_in_game_context"
                out.at[idx, "context_conflict_cause"] = "stale_team_not_in_game"
                continue
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
        team_pace = _team_metric(team_row, PACE_COLUMNS)
        opponent_pace = _team_metric(opp_row, PACE_COLUMNS)
        out.at[idx, "team_pace"] = _nullable(team_pace)
        out.at[idx, "opponent_pace"] = _nullable(opponent_pace)
        out.at[idx, "matchup_pace"] = _nullable(_average(team_pace, opponent_pace))
        out.at[idx, "team_off_rating"] = _nullable(_team_metric(team_row, OFF_RATING_COLUMNS))
        out.at[idx, "opponent_off_rating"] = _nullable(_team_metric(opp_row, OFF_RATING_COLUMNS))
        out.at[idx, "team_def_rating"] = _nullable(_team_metric(team_row, DEF_RATING_COLUMNS))
        out.at[idx, "opponent_def_rating"] = _nullable(_team_metric(opp_row, DEF_RATING_COLUMNS))
        out.at[idx, "team_net_rating"] = _nullable(_net_rating(team_row))
        out.at[idx, "opponent_net_rating"] = _nullable(_net_rating(opp_row))
        team_rest = rest_lookup.get(team)
        opponent_rest = rest_lookup.get(opponent)
        out.at[idx, "rest_days"] = float(team_rest) if team_rest is not None else _team_metric(team_row, ("rest_days", "team_rest_days"))
        out.at[idx, "opponent_rest_days"] = (
            float(opponent_rest) if opponent_rest is not None else _team_metric(opp_row, ("rest_days", "team_rest_days"))
        )
        rest_days = _float(out.at[idx, "rest_days"])
        opp_rest_days = _float(out.at[idx, "opponent_rest_days"])
        out.at[idx, "rest_edge"] = _nullable(rest_days - opp_rest_days if rest_days is not None and opp_rest_days is not None else None)
        out.at[idx, "is_back_to_back"] = bool(rest_days == 0.0) if rest_days is not None else pd.NA
        out.at[idx, "opponent_is_back_to_back"] = bool(opp_rest_days == 0.0) if opp_rest_days is not None else pd.NA

        market_payload = markets.get((_text(out.at[idx, "game_id"]), team), {})
        opponent_market_payload = markets.get((_text(out.at[idx, "game_id"]), opponent), {})
        game_payload = markets.get((_text(out.at[idx, "game_id"]), ""), {})
        for column in ("implied_team_total", "spread"):
            if column in market_payload:
                out.at[idx, column] = market_payload[column]
        if "implied_team_total" in opponent_market_payload:
            out.at[idx, "opponent_implied_team_total"] = opponent_market_payload["implied_team_total"]
        if "game_total" in game_payload:
            out.at[idx, "game_total"] = game_payload["game_total"]

        pace_signal = _pace_signal(
            out.at[idx, "team_pace"],
            out.at[idx, "opponent_pace"],
            out.at[idx, "matchup_pace"],
        )
        defense_signal = _defense_signal(
            out.at[idx, "opponent_def_rating"],
            out.at[idx, "opponent_net_rating"],
        )
        rest_signal = _rest_signal(
            out.at[idx, "rest_days"],
            out.at[idx, "opponent_rest_days"],
            out.at[idx, "is_back_to_back"],
            out.at[idx, "opponent_is_back_to_back"],
        )
        playoff_signal = _playoff_signal(out.at[idx, "postseason"])
        out.at[idx, "pace_context_signal"] = pace_signal
        out.at[idx, "defense_context_signal"] = defense_signal
        out.at[idx, "rest_context_signal"] = rest_signal
        out.at[idx, "playoff_context_signal"] = playoff_signal
        out.at[idx, "overall_context_signal"] = _overall_signal(
            (pace_signal, defense_signal, rest_signal, playoff_signal)
        )
        out.at[idx, "context_pick_alignment"] = _context_pick_alignment(
            row.get("selection"),
            out.at[idx, "overall_context_signal"],
        )
        out.at[idx, "context_caution_level"] = _context_caution_level(
            row.get("selection"),
            out.at[idx, "overall_context_signal"],
            out.at[idx, "context_pick_alignment"],
        )
        out.at[idx, "playoff_only_high_caution"] = _playoff_only_high_caution(out.loc[idx])
        out.at[idx, "context_conflict_cause"] = _context_conflict_cause(out.loc[idx])
        out.at[idx, "context_preview_applied"] = False

    out = mark_identity_quarantine_fields(out)
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
        "total_candidates": int(len(df)),
        "candidates_with_game_id": has_value("game_id"),
        "candidates_with_opponent": has_text("opponent"),
        "candidates_with_home_away": has_text("home_away"),
        "candidates_with_postseason": has_value("postseason"),
        "candidates_with_rest_days": has_any(("rest_days", "opponent_rest_days")),
        "candidates_with_back_to_back": has_true(("is_back_to_back", "opponent_is_back_to_back")),
        "candidates_with_def_rating": has_any(("team_def_rating", "opponent_def_rating")),
        "candidates_with_off_rating": has_any(("team_off_rating", "opponent_off_rating")),
        "candidates_with_pace": has_any(("team_pace", "opponent_pace")),
    }
    coverage["missing_fields_breakdown"] = {
        column: int(len(df) - has_value(column))
        for column in GAME_CONTEXT_COLUMNS
        if column in df.columns and has_value(column) < len(df)
    }
    coverage["missing_fields"] = list(coverage["missing_fields_breakdown"].keys())
    if "candidate_team_not_in_game" in df.columns:
        coverage["candidate_team_not_in_game_count"] = int(
            df["candidate_team_not_in_game"].map(lambda value: _text(value).lower() == "true").sum()
        )
    else:
        coverage["candidate_team_not_in_game_count"] = 0
    if "game_context_suppressed" in df.columns:
        coverage["game_context_suppressed_count"] = int(
            df["game_context_suppressed"].map(lambda value: _text(value).lower() == "true").sum()
        )
    else:
        coverage["game_context_suppressed_count"] = 0
    if "playoff_only_high_caution" in df.columns:
        coverage["playoff_only_high_caution_count"] = int(
            df["playoff_only_high_caution"].map(lambda value: _text(value).lower() == "true").sum()
        )
    else:
        coverage["playoff_only_high_caution_count"] = 0
    cause_counts = {bucket: 0 for bucket in CONTEXT_CONFLICT_CAUSE_BUCKETS}
    if "context_conflict_cause" in df.columns:
        raw_counts = (
            df["context_conflict_cause"]
            .map(lambda value: _text(value).lower())
            .loc[lambda series: series != ""]
            .value_counts()
            .to_dict()
        )
        for cause, count in raw_counts.items():
            cause_counts[cause] = int(count)
    coverage["context_conflict_cause_counts"] = cause_counts
    coverage["stale_team_not_in_game_count"] = int(cause_counts.get("stale_team_not_in_game", 0))
    identity_counts = identity_quarantine_reason_counts(df)
    coverage["identity_quarantine_count"] = int(sum(identity_counts.values()))
    coverage["identity_quarantine_reason_counts"] = identity_counts
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
        "sample_rows": candidates[
            [
                c
                for c in [
                    "player_name",
                    "team",
                    *GAME_CONTEXT_COLUMNS,
                    *GAME_CONTEXT_DIAGNOSTIC_COLUMNS,
                ]
                if c in candidates.columns
            ]
        ]
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
    caller = "courtvision.context.game_context:write_game_context_outputs"
    log_prediction_artifact_write(
        requested_prediction_date=prediction_date,
        output_path=json_path,
        caller=caller,
        artifact_label="game_context_json",
    )
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    sample_lines = []
    for sample in payload["sample_rows"][:8]:
        sample_lines.append(
            "- "
            f"{sample.get('player_name', '')} ({sample.get('team', '')} vs {sample.get('opponent', '')}, "
            f"{sample.get('home_away', '')}): rest={sample.get('rest_days')} "
            f"opp_rest={sample.get('opponent_rest_days')} pace={sample.get('matchup_pace')} "
            f"postseason={sample.get('postseason')} "
            f"signals=pace:{sample.get('pace_context_signal')} "
            f"defense:{sample.get('defense_context_signal')} "
            f"rest:{sample.get('rest_context_signal')} "
            f"playoff:{sample.get('playoff_context_signal')} "
            f"overall:{sample.get('overall_context_signal')} "
            f"alignment:{sample.get('context_pick_alignment')} "
            f"suppressed:{sample.get('game_context_suppressed')} "
            f"reason:{sample.get('game_context_suppression_reason')} "
            f"conflict_cause:{sample.get('context_conflict_cause')} "
            f"applied:{sample.get('context_preview_applied')}"
        )
    lines = [
        f"Game Context Diagnostics - {prediction_date}",
        "",
        "Coverage Summary",
        f"total_candidates: {payload.get('total_candidates', payload.get('rows', 0))}",
        f"game_context_rows: {payload['rows']}",
        f"candidates_with_game_id: {payload.get('candidates_with_game_id', 0)}",
        f"candidates_with_opponent: {payload['candidates_with_opponent']}",
        f"candidates_with_home_away: {payload.get('candidates_with_home_away', 0)}",
        f"candidates_with_postseason: {payload['candidates_with_postseason']}",
        f"candidates_with_rest_days: {payload['candidates_with_rest_days']}",
        f"candidates_with_back_to_back: {payload.get('candidates_with_back_to_back', 0)}",
        f"candidates_with_pace: {payload['candidates_with_pace']}",
        f"candidates_with_def_rating: {payload['candidates_with_def_rating']}",
        f"candidates_with_off_rating: {payload.get('candidates_with_off_rating', 0)}",
        "",
        "Safety Diagnostics",
        f"candidate_team_not_in_game_count: {payload.get('candidate_team_not_in_game_count', 0)}",
        f"game_context_suppressed_count: {payload.get('game_context_suppressed_count', 0)}",
        f"playoff_only_high_caution_count: {payload.get('playoff_only_high_caution_count', 0)}",
        "context_conflict_cause_counts:",
        json.dumps(payload.get("context_conflict_cause_counts", {}), indent=2, default=str),
        "",
        "Missing Fields",
        json.dumps(payload.get("missing_fields_breakdown", {}), indent=2, default=str),
        "",
        "Sample Rows",
        *(sample_lines or ["- none"]),
        "",
        "Preview signals are passive labels only and are not used in model math.",
        "",
        "Mode: passive diagnostics only. No projections, confidence, selection, elite, or Kelly logic changed.",
    ]
    log_prediction_artifact_write(
        requested_prediction_date=prediction_date,
        output_path=txt_path,
        caller=caller,
        artifact_label="game_context_report",
    )
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, txt_path, payload
