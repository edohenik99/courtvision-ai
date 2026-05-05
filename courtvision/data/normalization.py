from __future__ import annotations

import math
from typing import Any, Callable

import pandas as pd


FloatParser = Callable[[Any], float | None]
MarketMapper = Callable[[Any], str | None]
StatKeyMapper = Callable[[Any], str | None]


def _is_iso_datetime(value: Any) -> bool:
    """Heuristic to detect if a value is an ISO-8601 datetime string masquerading as status."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if len(text) < 10:
        return False
    # Must contain date separator, time separator, and time designator
    if "T" not in text or ":" not in text:
        return False
    # Must look like a date (YYYY-MM-DD) with optional time offset
    parts = text.split("T")
    if len(parts) < 2:
        return False
    date_part = parts[0]
    if len(date_part) < 10 or date_part[4] != "-" or date_part[7] != "-":
        return False
    return True


def parse_minutes(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    if ":" not in text:
        try:
            return float(text)
        except ValueError:
            return 0.0
    minutes, seconds, *_rest = (text.split(":") + ["0"])[:2]
    try:
        return int(minutes) + int(seconds) / 60.0
    except ValueError:
        return 0.0


def safe_float(value: Any) -> float | None:
    if value is None or value is pd.NA:
        return None
    if pd.isna(value):
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def infer_opponent_from_game(game: dict[str, Any], team_abbr: str) -> str:
    if not isinstance(game, dict):
        return ""

    home = game.get("home_team", {})
    visitor = game.get("visitor_team", {})

    home_abbr = home.get("abbreviation") if isinstance(home, dict) else ""
    visitor_abbr = visitor.get("abbreviation") if isinstance(visitor, dict) else ""

    if team_abbr == home_abbr:
        return str(visitor_abbr or "")
    if team_abbr == visitor_abbr:
        return str(home_abbr or "")
    return ""


def normalize_game_row(row: dict[str, Any]) -> dict[str, Any]:
    home = row.get("home_team", {}) or {}
    away = row.get("visitor_team", {}) or {}
    return {
        "game_id": int(row.get("id", 0) or 0),
        "date": str(row.get("date", "")),
        "status": str(row.get("status", "")),
        "home_team_id": int(home.get("id", 0) or 0),
        "home_team": str(home.get("abbreviation", "UNK")),
        "away_team_id": int(away.get("id", 0) or 0),
        "away_team": str(away.get("abbreviation", "UNK")),
    }


def normalize_player_stat_row(row: dict[str, Any]) -> dict[str, Any]:
    player = row.get("player", {}) or {}
    game = row.get("game", {}) or {}
    team = row.get("team", {}) or {}
    first = str(player.get("first_name", "")).strip()
    last = str(player.get("last_name", "")).strip()
    team_abbr = str(team.get("abbreviation", "UNK"))
    return {
        "player_id": int(player.get("id", 0) or 0),
        "player_name": f"{first} {last}".strip(),
        "team_id": int(team.get("id", 0) or 0),
        "team": team_abbr,
        "opponent_abbr": infer_opponent_from_game(game, team_abbr),
        "game_id": int(game.get("id", 0) or 0),
        "game_date": str(game.get("date", "")),
        "minutes": parse_minutes(row.get("min")),
        "points": float(row.get("pts", 0.0) or 0.0),
        "rebounds": float(row.get("reb", 0.0) or 0.0),
        "assists": float(row.get("ast", 0.0) or 0.0),
        "threes": float(row.get("fg3m", 0.0) or 0.0),
        "steals": float(row.get("stl", 0.0) or 0.0),
        "blocks": float(row.get("blk", 0.0) or 0.0),
    }


def normalize_stats_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        player = row.get("player", {}) if isinstance(row.get("player"), dict) else {}
        game = row.get("game", {}) if isinstance(row.get("game"), dict) else {}
        team = row.get("team", {}) if isinstance(row.get("team"), dict) else {}

        player_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
        if not player_name:
            player_name = str(row.get("player_name", "")).strip()

        team_abbr = str(team.get("abbreviation") or row.get("team_abbr") or "").strip()
        opponent_abbr = infer_opponent_from_game(game, team_abbr)

        rows.append(
            {
                "game_id": game.get("id") or row.get("game_id"),
                "game_date": game.get("date") or row.get("game_date"),
                "player_id": player.get("id") or row.get("player_id"),
                "player_name": player_name,
                "team_abbr": team_abbr,
                "opponent_abbr": opponent_abbr,
                "min": parse_minutes(row.get("min")),
                "pts": safe_float(row.get("pts")),
                "reb": safe_float(row.get("reb")),
                "ast": safe_float(row.get("ast")),
                "stl": safe_float(row.get("stl")),
                "blk": safe_float(row.get("blk")),
                "fg3m": safe_float(row.get("fg3m")),
                "turnover": safe_float(row.get("turnover")),
                "fga": safe_float(row.get("fga")),
                "fg3a": safe_float(row.get("fg3a")),
                "fta": safe_float(row.get("fta")),
            }
        )

    out = pd.DataFrame(rows)
    out["game_date"] = pd.to_datetime(out["game_date"], errors="coerce", utc=True).dt.tz_convert(None)
    out = out.dropna(subset=["player_id", "player_name", "team_abbr"])
    return out


def normalize_games_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["game_id", "home_team_abbr", "visitor_team_abbr", "status", "date", "datetime"])

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        home = row.get("home_team", {}) if isinstance(row.get("home_team"), dict) else {}
        visitor = row.get("visitor_team", {}) if isinstance(row.get("visitor_team"), dict) else {}
        # Guard against upstream APIs that put an ISO datetime in the status field
        status = row.get("status")
        datetime_val = row.get("datetime")
        if _is_iso_datetime(status):
            # The status field contains a datetime string; treat as unknown and preserve the datetime
            if datetime_val is None or str(datetime_val).strip() == "":
                datetime_val = status
            status = "unknown"
        # Preserve date/datetime from upstream so downstream slate-lock can validate
        rows.append(
            {
                "game_id": row.get("id"),
                "home_team_abbr": home.get("abbreviation") or row.get("home_team_abbr"),
                "visitor_team_abbr": visitor.get("abbreviation") or row.get("visitor_team_abbr"),
                "status": status,
                "date": row.get("date"),
                "datetime": datetime_val,
            }
        )

    out = pd.DataFrame(rows)
    return out.dropna(subset=["game_id", "home_team_abbr", "visitor_team_abbr"])


def normalize_odds_frame(
    df: pd.DataFrame,
    *,
    map_market_type: MarketMapper,
    market_to_stat_key: StatKeyMapper,
    to_float: FloatParser = safe_float,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "game_id",
                "bookmaker",
                "market_type",
                "raw_stat_key",
                "market_alias",
                "player_id",
                "player_name",
                "team",
                "line",
                "over_odds",
                "under_odds",
                "odds",
                "selection",
            ]
        )

    out = df.copy()
    if "player_id" not in out.columns:
        out["player_id"] = None
    # Handle missing raw_market_name - map from BallDontLie fields
    if "raw_market_name" not in out.columns:
        # Try to map from prop_type or market fields
        if "prop_type" in out.columns:
            out["raw_market_name"] = out["prop_type"]
        elif "market" in out.columns:
            out["raw_market_name"] = out["market"]
        else:
            out["raw_market_name"] = None
    out["market_type"] = out["raw_market_name"].apply(map_market_type)
    out["raw_stat_key"] = out["market_type"].apply(market_to_stat_key)
    out["market_alias"] = out["market_type"]
    out["player_id"] = pd.to_numeric(out["player_id"], errors="coerce")
    out["line"] = out["line"].apply(to_float)
    out["over_odds"] = out["over_odds"].apply(to_float)
    out["under_odds"] = out["under_odds"].apply(to_float)
    out["odds"] = out["odds"].apply(to_float)

    valid = out["market_type"].notna()
    return out[valid].reset_index(drop=True)


def normalize_injuries_frame(injuries_raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize injuries to a canonical schema with row-level diagnostics.

    Accepts these shapes:
      - dotted SDK fields: ``player.id``, ``player.first_name``, ``player.last_name``, ``player.team_id``
      - nested object/dict: ``player`` column containing a dict/object
      - flat fields: ``player_id``, ``first_name``, ``last_name``, ``player_name``
      - team fields: ``team_abbr``, ``team``, ``team_id``, ``player.team_id``
      - status fields: ``status``, ``injury_status``
      - description fields: ``description``, ``injury_description``, ``note``, ``notes``
      - return fields: ``return_date``, ``expected_return``, ``expected_return_date``
    """
    expected_cols = [
        "player_id", "first_name", "last_name", "player_name",
        "team_id", "team_abbr", "status", "description", "return_date",
        "injury_normalized", "injury_rejection_reason",
    ]
    if injuries_raw.empty:
        return pd.DataFrame(columns=expected_cols)

    injuries = injuries_raw.copy()

    # --- flatten nested player dict/object if present ---
    if "player" in injuries.columns and "player_name" not in injuries.columns:
        def _extract_player_field(row: pd.Series, field: str) -> Any:
            p = row.get("player")
            if isinstance(p, dict):
                return p.get(field)
            if p is not None and hasattr(p, field):
                return getattr(p, field, None)
            return None

        if "player_id" not in injuries.columns:
            injuries["player_id"] = injuries.apply(lambda r: _extract_player_field(r, "id"), axis=1)
        if "first_name" not in injuries.columns:
            injuries["first_name"] = injuries.apply(lambda r: _extract_player_field(r, "first_name"), axis=1).astype(str)
        if "last_name" not in injuries.columns:
            injuries["last_name"] = injuries.apply(lambda r: _extract_player_field(r, "last_name"), axis=1).astype(str)
        if "team_id" not in injuries.columns:
            injuries["team_id"] = injuries.apply(
                lambda r: (
                    (r.get("player") or {}).get("team", {}).get("id")
                    if isinstance(r.get("player"), dict) else
                    getattr(getattr(r.get("player"), "team", None), "id", None)
                ),
                axis=1,
            )
        if "team_abbr" not in injuries.columns:
            injuries["team_abbr"] = injuries.apply(
                lambda r: (
                    str((r.get("player") or {}).get("team", {}).get("abbreviation", "")).strip()
                    if isinstance(r.get("player"), dict) else
                    str(getattr(getattr(r.get("player"), "team", None), "abbreviation", "") or "").strip()
                ),
                axis=1,
            )

    # --- rename dotted SDK fields to flat names ---
    rename_map = {
        "player.id": "player_id",
        "player.first_name": "first_name",
        "player.last_name": "last_name",
        "player.team_id": "team_id",
    }
    injuries = injuries.rename(columns={k: v for k, v in rename_map.items() if k in injuries.columns})

    # --- unify alternative field names ---
    if "status" not in injuries.columns and "injury_status" in injuries.columns:
        injuries["status"] = injuries["injury_status"]
    if "description" not in injuries.columns:
        for alt in ("injury_description", "note", "notes"):
            if alt in injuries.columns:
                injuries["description"] = injuries[alt]
                break
    if "return_date" not in injuries.columns:
        for alt in ("expected_return", "expected_return_date"):
            if alt in injuries.columns:
                injuries["return_date"] = injuries[alt]
                break
    if "team_abbr" not in injuries.columns:
        for alt in ("team", ):
            if alt in injuries.columns:
                injuries["team_abbr"] = injuries[alt]
                break

    # --- build player_name from first/last if missing ---
    if "player_name" not in injuries.columns:
        injuries["player_name"] = ""
    for i, row in injuries.iterrows():
        if not str(row.get("player_name", "")).strip():
            fname = str(row.get("first_name", "")).strip()
            lname = str(row.get("last_name", "")).strip()
            injuries.at[i, "player_name"] = f"{fname} {lname}".strip()

    # --- ensure expected columns exist with safe defaults ---
    for col in ["player_id", "team_id", "team_abbr", "status", "description", "return_date"]:
        if col not in injuries.columns:
            injuries[col] = pd.NA

    # --- row-level validation / diagnostics ---
    def _row_diag(row: pd.Series) -> tuple[bool, str]:
        import pandas as pd
        _pn = row.get("player_name")
        player_name = str(_pn).strip() if _pn is not None and not pd.isna(_pn) else ""
        if not player_name:
            return False, "missing_player_identity"
        team_id = row.get("team_id")
        _ta = row.get("team_abbr")
        team_abbr = str(_ta).strip() if _ta is not None and not pd.isna(_ta) else ""
        has_team_id = team_id is not None and not pd.isna(team_id)
        if not has_team_id and not team_abbr:
            return False, "missing_team_identity"
        _st = row.get("status")
        status = str(_st).strip() if _st is not None and not pd.isna(_st) else ""
        if not status:
            return False, "missing_status"
        return True, ""

    diags = injuries.apply(_row_diag, axis=1, result_type="expand")
    diags.columns = ["injury_normalized", "injury_rejection_reason"]
    injuries = pd.concat([injuries.reset_index(drop=True), diags], axis=1)

    # Coerce types for downstream safety
    injuries["status"] = injuries.get("status", pd.Series("", index=injuries.index)).fillna("").astype(str)
    injuries["description"] = injuries.get("description", pd.Series("", index=injuries.index)).fillna("").astype(str)
    injuries["return_date"] = injuries.get("return_date", pd.Series("", index=injuries.index)).fillna("").astype(str)
    injuries["player_name"] = injuries.get("player_name", pd.Series("", index=injuries.index)).fillna("").astype(str).str.strip()

    # Keep only rows that passed validation
    injuries = injuries[injuries["injury_normalized"]].copy()
    return injuries.reset_index(drop=True)


def normalize_games_schema(games_raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize BallDontLie games payload to canonical internal schema.

    Handles multiple input shapes:
    - Nested dict objects: home_team/visitor_team containing id, abbreviation, name
    - Flat id columns: home_team_id, visitor_team_id (BallDontLie naming)
    - Pre-normalized: already has canonical columns

    Canonical output schema:
    - game_id: int
    - home_team_id: int
    - home_team_abbr: str
    - home_team_name: str
    - visitor_team_id: int
    - visitor_team_abbr: str
    - visitor_team_name: str
    - game_date: str (ISO format)
    - status: str

    Args:
        games_raw: Raw games DataFrame from BallDontLie API or other source

    Returns:
        Normalized games DataFrame with canonical schema

    Raises:
        ValueError: If required team fields cannot be derived from any source
    """
    if not isinstance(games_raw, pd.DataFrame) or games_raw.empty:
        return pd.DataFrame()

    games = games_raw.copy()

    # Ensure game_id exists
    if "id" in games.columns and "game_id" not in games.columns:
        games["game_id"] = games["id"]
    if "game_id" not in games.columns:
        raise ValueError("Cannot normalize games: missing 'id' or 'game_id' column")

    games["game_id"] = pd.to_numeric(games["game_id"], errors="coerce")

    # Normalize home team fields
    games = _extract_team_fields(
        games,
        prefix="home",
        nested_col="home_team",
        id_col="home_team_id",
        abbr_col="home_team_abbr",
        name_col="home_team_name",
    )

    # Normalize visitor team fields (BallDontLie uses "visitor", not "away")
    games = _extract_team_fields(
        games,
        prefix="visitor",
        nested_col="visitor_team",
        id_col="visitor_team_id",
        abbr_col="visitor_team_abbr",
        name_col="visitor_team_name",
    )

    # Handle legacy "away_team" naming if present (convert to visitor)
    if "away_team_id" in games.columns and "visitor_team_id" not in games.columns:
        games["visitor_team_id"] = games["away_team_id"]
    if "away_team" in games.columns and "visitor_team" not in games.columns:
        # Could be nested or flat
        games["visitor_team"] = games["away_team"]
        games = _extract_team_fields(
            games,
            prefix="visitor",
            nested_col="visitor_team",
            id_col="visitor_team_id",
            abbr_col="visitor_team_abbr",
            name_col="visitor_team_name",
        )

    # Validate required fields exist (accept IDs OR abbreviations)
    # Either (home_team_id + visitor_team_id) OR (home_team_abbr + visitor_team_abbr)
    has_ids = ("home_team_id" in games.columns and not games["home_team_id"].isna().all() and
               "visitor_team_id" in games.columns and not games["visitor_team_id"].isna().all())
    has_abbrs = ("home_team_abbr" in games.columns and not games["home_team_abbr"].isna().all() and
                 "visitor_team_abbr" in games.columns and not games["visitor_team_abbr"].isna().all())

    if not has_ids and not has_abbrs:
        raise ValueError(
            f"Cannot normalize games: missing required team fields. "
            f"Need either (home_team_id + visitor_team_id) or (home_team_abbr + visitor_team_abbr). "
            f"Available columns: {list(games.columns)}"
        )

    # Ensure abbreviations are always present as canonical matching fields
    if not has_abbrs and has_ids:
        # Use IDs as fallback for abbreviations if needed
        if "home_team_abbr" not in games.columns or games["home_team_abbr"].isna().all():
            games["home_team_abbr"] = games["home_team_id"].astype(str)
        if "visitor_team_abbr" not in games.columns or games["visitor_team_abbr"].isna().all():
            games["visitor_team_abbr"] = games["visitor_team_id"].astype(str)

    # Normalize date field
    if "date" in games.columns and "game_date" not in games.columns:
        games["game_date"] = games["date"]
    if "game_date" not in games.columns:
        games["game_date"] = None

    # Normalize status
    if "status" not in games.columns:
        games["status"] = "unknown"

    # Keep original raw columns for debugging (prefixed with raw_).
    # Preserve even columns that also participate in canonical extraction so
    # callers can inspect the source payload after normalization.
    for col in games_raw.columns:
        raw_col = f"raw_{col}"
        if raw_col not in games.columns:
            games[raw_col] = games_raw[col]

    return games.reset_index(drop=True)


def _extract_team_fields(
    df: pd.DataFrame,
    prefix: str,
    nested_col: str,
    id_col: str,
    abbr_col: str,
    name_col: str,
) -> pd.DataFrame:
    """Extract team fields from nested dict or flat columns.

    Args:
        df: DataFrame to process
        prefix: Team prefix ("home" or "visitor")
        nested_col: Column name containing nested dict
        id_col: Target column name for team ID
        abbr_col: Target column name for team abbreviation
        name_col: Target column name for team name

    Returns:
        DataFrame with extracted fields added
    """
    # If already normalized (has id OR abbr), skip extraction
    has_id = id_col in df.columns and not df[id_col].isna().all()
    has_abbr = abbr_col in df.columns and not df[abbr_col].isna().all()
    if has_id or has_abbr:
        return df

    # Try nested dict extraction
    if nested_col in df.columns:
        # Check if column contains dict-like objects
        sample = df[nested_col].dropna().iloc[0] if not df[nested_col].isna().all() else None
        if sample and isinstance(sample, (dict,)):
            # Extract from nested dict
            df[id_col] = df[nested_col].apply(lambda x: x.get("id") if isinstance(x, dict) else None)
            df[abbr_col] = df[nested_col].apply(lambda x: x.get("abbreviation") if isinstance(x, dict) else None)
            df[name_col] = df[nested_col].apply(lambda x: x.get("name") if isinstance(x, dict) else None)
            return df

    # Try alternative column names
    alt_id_cols = [f"{prefix}_id", f"{prefix}_team_id", f"{prefix}TeamId"]
    alt_abbr_cols = [f"{prefix}_abbr", f"{prefix}_abbreviation", f"{prefix}_team_abbr"]
    alt_name_cols = [f"{prefix}_name", f"{prefix}_team_name"]

    for col in alt_id_cols:
        if col in df.columns and id_col not in df.columns:
            df[id_col] = pd.to_numeric(df[col], errors="coerce")
            break

    for col in alt_abbr_cols:
        if col in df.columns and abbr_col not in df.columns:
            df[abbr_col] = df[col].astype(str)
            break

    for col in alt_name_cols:
        if col in df.columns and name_col not in df.columns:
            df[name_col] = df[col].astype(str)
            break

    return df
